"""단지 목록 기준(부동산지인 방식) 입주 · 수요 — 전국 · 시도 · 시군구 공통.

- 입주 실적: K-apt(공동주택관리정보시스템) '관리비공개의무단지 기본정보'(매주 게시)의 단지별 사용승인일 · 세대수.
  의무관리대상(300세대 이상, 150세대 이상 승강기 · 중앙난방 등)과 100세대 이상 가입 단지가 대상이라
  소규모 단지는 빠진다. 연립 · 다세대는 뺀다.
- 입주 예정: 청약홈 입주예정 엑셀(supply.plans, 향후 2년)의 분양 · 분양임대 단지(순수 임대 제외),
  그 뒤 달은 청약홈 분양 공고의 입주예정월 · 공급세대수(ah_movein).
- 수요: 행정안전부 주민등록인구(전체 시군구 현황, 연말 · 최근 달) × 0.5%.

도(道)는 시 · 군 단위(일반구는 시로 합산), 특별 · 광역시는 구 · 군 단위로 묶는다.
행정구역 개편은 지금 이름으로 맞춘다: 청원군→청주시, 여주군→여주시, 당진군→당진시, 인천 남구→미추홀구,
경북 군위군→대구 군위군. 인천 중구 · 동구(→제물포구 · 영종구)와 서구(→서해구 · 검단구)는 과거와 이어 보기 위해 묶는다.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time

import openpyxl

from collect import log, now_kst, num, req  # noqa: E402

KAPT = "https://www.k-apt.go.kr"
JUMIN = "https://jumin.mois.go.kr"
METRO = {"서울", "부산", "대구", "인천", "대전", "울산"}
GU_CITY = re.compile(r"^(수원|성남|안양|부천|안산|고양|용인|화성|청주|천안|전주|포항|창원)시?\s*(\S+구)$")
GWANGJU_GU = {"동구", "서구", "남구", "북구", "광산구"}
INCHEON = {"중구": "제물포구·영종구", "동구": "제물포구·영종구", "제물포구": "제물포구·영종구", "영종구": "제물포구·영종구",
           "서구": "서해구·검단구", "서해구": "서해구·검단구", "검단구": "서해구·검단구", "남구": "미추홀구"}
RENAME = {("충북", "청원군"): "청주시", ("경기", "여주군"): "여주시", ("충남", "당진군"): "당진시"}
SKIP_KIND = {"연립주택", "다세대", "도시형 생활주택(연립주택)", "도시형 생활주택(다세대)"}


def unit_of(sd: str | None, raw: str | None) -> tuple[str, str] | None:
    """(시도 약칭, 원래 시군구 표기) → (시도, 단위 이름). 세종 · 미상은 None."""
    if not sd or sd == "세종":
        return None
    raw = re.sub(r"특례시$", "시", (raw or "").strip())
    if not raw or "출장소" in raw:
        return None
    if sd == "경북" and raw == "군위군":
        return ("대구", "군위군")
    if sd == "인천":
        return (sd, INCHEON.get(raw, raw))
    if sd in METRO:
        return (sd, raw)
    if sd == "전남광주":
        return (sd, "광주 " + raw if raw in GWANGJU_GU else raw)
    m = GU_CITY.match(raw)
    if m:
        return (sd, m.group(1) + "시")
    first = raw.split()[0]
    return (sd, RENAME.get((sd, first), first))


def unit_from_addr(addr: str | None, short) -> tuple[str, str] | None:
    """청약홈 주소 '경기도 수원시 장안구 …' → (경기, 수원시)"""
    tok = (addr or "").split()
    if len(tok) < 2:
        return None
    return unit_of(short(tok[0]), tok[1])


def kapt_completions(s, short) -> tuple[dict, dict, dict]:
    """K-apt 단지 기본정보 → {(시도, 'YYYYMM'): 세대수}, {(시도, 단위, 'YYYYMM'): 세대수}, 메타"""
    page = req(s, "GET", KAPT + "/web/board/webReference/boardList.do", timeout=60).text
    tok = re.search(r'name="_csrf"\s+content="([^"]+)"', page)
    hdr = re.search(r'name="_csrf_header"\s+content="([^"]+)"', page)
    if not tok:
        raise RuntimeError("K-apt 자료실 접속 실패(보안 토큰 없음)")
    token, header = tok.group(1), (hdr.group(1) if hdr else "X-CSRF-TOKEN")
    ajax = {header: token, "X-Requested-With": "XMLHttpRequest"}
    lst = req(s, "POST", KAPT + "/web/board/webReference/boardListAjax.do", headers=ajax, timeout=60,
              data={"scode": "01", "boardType": "03", "pageNo": "1", "stype": "", "keyword": "", "_csrf": token}).text
    m = re.search(r"goCheck\((\d+),\s*0\)[^>]*>\s*K-apt\s*관리비공개의무단지\s*기본정보\(([\d.]+)\)", lst)
    if not m:
        raise RuntimeError("K-apt 자료실에서 단지 기본정보 게시물을 찾지 못함")
    seq, basis = m.group(1), m.group(2).rstrip(".")
    form = {"boardType": "03", "pageNo": "1", "stype": "", "keyword": "", "seq": seq, "scode": "01", "boardPwd": "",
            "boardSecret": "0", "_csrf": token}
    view = req(s, "POST", KAPT + "/web/board/webReference/boardView.do", data=form, timeout=60).text
    t2 = re.search(r'name="_csrf"\s+content="([^"]+)"', view)
    if t2:
        token = t2.group(1)
        ajax[header] = token
    form["_csrf"] = token
    lf = {k: v for k, v in form.items() if k != "boardSecret"}
    r = req(s, "POST", KAPT + "/web/board/webReference/fileListData.do?seq=BOARD_FILE", timeout=60,
            data=json.dumps({"data": lf}, ensure_ascii=False).encode(),
            headers={header: token, "Content-Type": "application/json;charset=UTF-8", "Accept": "application/json, text/javascript, */*"})
    try:
        files = [f for f in (r.json().get("data") or []) if str(f.get("fileName", "")).endswith(".xlsx")]
    except ValueError:
        files = []
    # 목록 응답이 없으면 게시일로 파일 이름을 만든다(예: 20261009_단지_기본정보.xlsx)
    f = files[0] if files else {"seq": 1, "fileName": basis.replace(".", "") + "_단지_기본정보.xlsx"}
    r = req(s, "GET", KAPT + "/cmm/file/BOARD/fileDownload.do", params={"key": f["seq"], "fileName": f["fileName"]},
            headers={header: token}, timeout=180)
    if r.content[:2] != b"PK":
        raise RuntimeError("K-apt 단지 기본정보 파일이 엑셀이 아님")
    wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    ws.reset_dimensions()          # 파일에 적힌 범위가 'A1'뿐이라 다시 잡아야 전체 행을 읽는다
    head, out, out_sd, n, used = None, {}, {}, 0, 0
    for row in ws.iter_rows(values_only=True):
        if head is None:
            cells = [str(c).strip() if c is not None else "" for c in row]
            if "사용승인일" in cells and "세대수" in cells:
                head = {c: i for i, c in enumerate(cells)}
            continue
        n += 1
        g = lambda k: row[head[k]] if head.get(k) is not None and head[k] < len(row) else None  # noqa: E731
        if str(g("단지분류") or "").strip() in SKIP_KIND:
            continue
        d = re.sub(r"\D", "", str(g("사용승인일") or ""))[:6]
        hh = num(g("세대수"))
        sd = short(str(g("시도") or ""))
        if len(d) != 6 or not hh or not sd:
            continue
        u = unit_of(sd, str(g("시군구") or ""))
        sd = u[0] if u else sd                      # 군위군은 대구로
        out_sd[(sd, d)] = out_sd.get((sd, d), 0) + int(round(hh))
        if u:
            out[(u[0], u[1], d)] = out.get((u[0], u[1], d), 0) + int(round(hh))
        used += 1
    wb.close()
    if not head or not out:
        raise RuntimeError(f"K-apt 단지 기본정보에서 읽은 단지가 없음(머리행 {'있음' if head else '없음'}, {n}행)")
    return out_sd, out, {"basis": basis, "complexes": used, "rows": n, "file": f["fileName"]}


def jumin_units(s, short, first_year: int) -> tuple[dict, dict, str | None]:
    """주민등록 전체 시군구 현황 → {(시도, 단위, 연도|'latest'): 인구}, {(시도, 단위): 정렬 순서}, 최근 달"""
    req(s, "GET", JUMIN + "/statMonth.do", timeout=60)
    now = now_kst()
    base = {"sltOrgType": "1", "sltOrgLvl1": "A", "sltOrgLvl2": "A", "gender": "gender", "genderPer": "genderPer",
            "generation": "generation", "sltUndefType": "", "sltOrderType": "1", "sltOrderValue": "ASC"}
    out, order = {}, {}

    def read(kind, data):
        r = req(s, "POST", f"{JUMIN}/downloadCsv.do?searchYearMonth={kind}&xlsStats=2", data=data, timeout=180)
        for enc in ("cp949", "utf-8-sig"):
            try:
                return list(csv.reader(io.StringIO(r.content.decode(enc))))
            except UnicodeDecodeError:
                continue
        raise RuntimeError("시군구 인구 CSV 인코딩 오류")

    def take(rows, colkey):
        head = rows[0]
        for row in rows[1:]:
            if not row:
                continue
            m = re.match(r"\s*(.+?)\s*\((\d{10})\)\s*$", row[0])
            if not m:
                continue
            name, code = m.group(1).split(), m.group(2)
            if len(name) != 2:          # 시도 합계(1단어) · 일반구(3단어) 행은 건너뛴다
                continue
            u = unit_of(short(name[0]), name[1])
            if not u:
                continue
            order.setdefault(u, int(code[:5]))
            for i, h in enumerate(head):
                key = colkey(h)
                if key is not None and i < len(row):
                    v = num(row[i])
                    if v:
                        out[(u[0], u[1], key)] = out.get((u[0], u[1], key), 0) + v

    yr = read("year", dict(base, category="year", searchYearMonth="year", searchYearStart=str(first_year), searchMonthStart="12",
                           searchYearEnd=str(now.year - 1), searchMonthEnd="12"))
    take(yr, lambda h: int(m.group(1)) if (m := re.match(r"(\d{4})년_총인구수", h)) else None)
    latest = None
    for back in range(0, 4):
        y, mth = now.year, now.month - back
        while mth <= 0:
            y, mth = y - 1, mth + 12
        try:
            rows = read("month", dict(base, category="month", searchYearMonth="month", searchYearStart=str(y),
                                      searchMonthStart=f"{mth:02d}", searchYearEnd=str(y), searchMonthEnd=f"{mth:02d}"))
        except Exception:  # noqa: BLE001
            continue
        if len(rows) < 3 or not any(h.endswith("총인구수") for h in rows[0]):
            continue
        before = len(out)
        take(rows, lambda h: "latest" if h.endswith("총인구수") else None)
        if len(out) > before:
            latest = f"{y}-{mth:02d}"
            break
        time.sleep(1)
    return out, order, latest


PROV_RE = re.compile(r"(서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|경기도|"
                     r"강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|전라남도|경상북도|경상남도|제주특별자치도|"
                     r"\S+통합특별시)\s+(\S+)")
SKIP_NOTICE = re.compile(r"취소분|보류지|잔여|무순위|임의공급|계약취소|재공급|추가모집|사전청약")


def addr_unit(addr: str | None, short) -> tuple[str, str] | None:
    """'김포 풍무역세권 B4블록 (경기도 김포시 …)'처럼 사업지명이 앞에 와도 주소 부분을 찾아 단위를 정한다."""
    m = PROV_RE.search(addr or "")
    return unit_of(short(m.group(1)), m.group(2)) if m else None


def ah_movein(s, short, months: int = 30, max_fetch: int = 900) -> tuple[list[dict], dict]:
    """청약홈 분양 공고(최근 months개월)의 입주예정월 · 공급세대수. 단지 상세는 cache/movein.json에 쌓아 둔다."""
    import json as _json

    from collect import AH, CACHE, ah_detail, cells

    cache_f = CACHE / "movein.json"
    cache = _json.loads(cache_f.read_text()) if cache_f.exists() else {}
    ah_f = CACHE / "applyhome.json"
    if ah_f.exists():                      # 청약 현황 수집에서 이미 받은 상세는 다시 받지 않는다
        for pb, c in _json.loads(ah_f.read_text()).items():
            d = c.get("det") or {}
            if pb not in cache and d.get("movein"):
                cache[pb] = {"m": d["movein"], "u": d.get("units") or sum((t.get("tot") or 0) for t in d.get("types") or []),
                             "a": d.get("addr")}
    now = now_kst()
    rows, y, m = [], now.year, now.month
    for _ in range((months + 5) // 6):
        ey, em = y, m
        by, bm = y, m - 5
        while bm <= 0:
            by, bm = by - 1, bm + 12
        for page in range(1, 120):
            r = req(s, "POST", AH + "/ai/aia/selectAPTLttotPblancListView.do",
                    data={"beginPd": f"{by}{bm:02d}", "endPd": f"{ey}{em:02d}", "pageIndex": str(page)})
            trs = re.findall(r'(?s)<tr data-pbno="(\d+)" data-hmno="(\d+)" data-honm="([^"]*)">(.*?)</tr>', r.text)
            if not trs:
                break
            for pb, hm, nm, body in trs:
                c = cells(body)
                if len(c) >= 7:
                    rows.append({"pb": pb, "hm": hm, "name": nm.strip(), "sido": c[0], "sale": c[2], "notice": c[6]})
            if not re.search(rf"pageIndex={page + 1}\b", r.text):
                break
            time.sleep(0.3)
        y, m = by, bm - 1
        if m <= 0:
            y, m = y - 1, m + 12
    seen, lst = set(), []
    for x in rows:
        if x["pb"] in seen or SKIP_NOTICE.search(x["name"]) or "임대" in x["sale"]:
            continue
        seen.add(x["pb"]); lst.append(x)
    fetched = 0
    for x in lst:
        if x["pb"] in cache or fetched >= max_fetch:
            continue
        try:
            d = ah_detail(s, x["hm"], x["pb"])
            cache[x["pb"]] = {"m": d.get("movein"), "u": d.get("units") or sum((t.get("tot") or 0) for t in d.get("types") or []),
                              "a": d.get("addr")}
            fetched += 1
            time.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            log("청약홈 공고 상세 실패", x["name"], e)
    keep = {x["pb"] for x in lst}
    cache = {k: v for k, v in cache.items() if k in keep}
    CACHE.mkdir(exist_ok=True)
    cache_f.write_text(_json.dumps(cache, ensure_ascii=False, separators=(",", ":")))
    out = []
    for x in lst:
        c = cache.get(x["pb"])
        if not c or not c.get("m") or not c.get("u"):
            continue
        ym = re.sub(r"\D", "", c["m"])[:6]
        if len(ym) != 6:
            continue
        sd = short(x["sido"])
        u = addr_unit(c.get("a"), short)
        out.append({"name": x["name"], "sd": u[0] if u else sd, "unit": u[1] if u else None, "ym": ym, "n": int(c["u"])})
    log("청약홈 분양 공고(입주예정월)", len(lst), "건, 새로 받은 상세", fetched)
    return out, {"notices": len(lst), "fetched": fetched}
