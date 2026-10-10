"""아파트 입주(공급) · 수요 추이.

- 입주 실적: 국토교통 통계누리 주택건설실적통계(준공) '주택유형별 주택건설 준공실적(월계)'(formId 5373)의
  아파트 사용검사 실적을 시·도별로 연도 합산한다(2011년~). 한 번에 60개월까지만 조회되므로 나눠 받는다.
- 입주 예정: 청약홈 '입주(예정)정보 조회' 화면의 첨부 엑셀(한국부동산원 · 부동산R114, 단지별 입주예정월 · 세대수)을
  시·도별 · 연도별로 합산한다. 예정 자료가 시작하는 달부터는 실적 대신 예정을 쓴다(같은 달을 두 번 세지 않도록).
- 수요: 행정안전부 주민등록인구(연말 기준) × 0.5%. 올해 이후는 가장 최근 달 인구를 쓴다.
- 광주 · 전남은 2026년 통합(전남광주통합특별시) 이후 표기에 맞춰 과거 연도까지 '전남광주'로 합친다.
- 시 · 군 · 구 단위는 sgg.py(K-apt 단지 사용승인 · 청약홈 예정 주소 · 시군구 인구)로 따로 집계한다.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.parse
from collections import defaultdict

import openpyxl

from collect import AH, DATA, MOLIT, log, now_kst, num, req  # noqa: E402

JUMIN = "https://jumin.mois.go.kr"
DEMAND_RATE = 0.005
FIRST_YEAR = 2011
SIDO = ["서울", "부산", "대구", "인천", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남광주", "경북", "경남", "제주"]
CAP = {"서울", "인천", "경기"}
ALIAS = {"광주": "전남광주", "전남": "전남광주"}
LONG = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
        "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원도": "강원",
        "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북",
        "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주"}


def short(name: str) -> str | None:
    name = re.sub(r"\s*\(\d+\)\s*$", "", (name or "").strip())
    if "전남광주" in name:
        return "전남광주"
    s = LONG.get(name, name)
    s = ALIAS.get(s, s)
    return s if s in SIDO else None


def months_between(a: str, b: str):
    y, m = int(a[:4]), int(a[4:])
    while f"{y}{m:02d}" <= b:
        yield f"{y}{m:02d}"
        m += 1
        if m > 12:
            y, m = y + 1, 1


def completions(s, end_ym: str) -> tuple[dict, str | None]:
    """{(시도, 'YYYYMM'): 아파트 준공 호수}, 마지막 자료월"""
    try:
        req(s, "GET", f"{MOLIT}/portal/cate/statView.do?hRsId=468&hFormId=5373", tries=4, timeout=45, backoff=15)
    except Exception as e:  # noqa: BLE001
        log("통계누리(준공) 화면 열기 실패", e)
    out, last = {}, None
    ms = list(months_between(f"{FIRST_YEAR}01", end_ym))
    for i in range(0, len(ms), 60):
        a, b = ms[i], ms[min(i + 59, len(ms) - 1)]
        r = req(s, "GET", f"{MOLIT}/portal/stat/data.do?formId=5373&styleNum=1&apprYn=Y&startDate={a}&endDate={b}",
                tries=4, timeout=180, backoff=20)
        j = r.json()
        if not j.get("result"):
            raise RuntimeError(f"통계누리(준공) 응답 오류: {str(j)[:200]}")
        for x in j["data"]:
            if x.get("2") != "아파트" or x.get("3") != "아파트":
                continue
            sd = short(x["1"])
            if not sd:
                continue
            ym = re.sub(r"\D", "", x["0"])[:6]   # '2026-08 p)'(잠정치) 같은 표기 정리
            if len(ym) != 6:
                continue
            out[(sd, ym)] = out.get((sd, ym), 0) + (num(x["5"]) or 0)
            last = max(last or ym, ym)
        time.sleep(1)
    return out, last


def plans(s) -> tuple[dict, dict, dict]:
    """{(시도, 'YYYYMM'): 입주예정 세대수}, 메타(기준 시점 · 기간), {(시도, 시군구 단위, 'YYYYMM'): 세대수}"""
    from sgg import unit_from_addr
    page = req(s, "GET", AH + "/ai/aia/selectAPTMvnPrearngeHsHldcoList.do").text
    m = re.search(r'data-file="([^"]+\.xlsx)"', page)
    if not m:
        raise RuntimeError("청약홈 입주예정 첨부파일 이름을 찾지 못함")
    basis = re.search(r"기준\s*시점\s*:\s*([^<]+?)\s*<", page)
    r = req(s, "GET", AH + "/form/ai/" + urllib.parse.quote(m.group(1)), timeout=90)
    wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    out, n, yms, units = {}, 0, [], {}
    head = None
    for row in ws.iter_rows(values_only=True):
        if head is None:
            if row and "입주예정월" in [str(c).strip() if c else "" for c in row]:
                head = [str(c).strip() if c else "" for c in row]
            continue
        rec = dict(zip(head, row))
        ym, sd, cnt = str(rec.get("입주예정월") or "").strip(), short(str(rec.get("지역") or "")), num(rec.get("세대수"))
        if not re.fullmatch(r"\d{6}", ym) or not sd or not cnt:
            if ym and sd is None and rec.get("주소"):
                sd = short(str(rec["주소"]).split()[0])
            if not re.fullmatch(r"\d{6}", ym) or not sd or not cnt:
                continue
        out[(sd, ym)] = out.get((sd, ym), 0) + cnt
        u = unit_from_addr(str(rec.get("주소") or ""), short)
        if u:
            units[(u[0], u[1], ym)] = units.get((u[0], u[1], ym), 0) + cnt
        yms.append(ym)
        n += 1
    if not out:
        raise RuntimeError("입주예정 엑셀에서 읽은 단지가 없음")
    meta = {"basis": basis.group(1).strip() if basis else None, "from": min(yms), "to": max(yms), "complexes": n, "file": m.group(1)}
    return out, meta, units


def population(s) -> tuple[dict, str]:
    """{(시도, 연도): 연말 인구}, 최근 달 인구는 연도 키 'latest'로. 최근 달 'YYYY-MM' 반환"""
    req(s, "GET", JUMIN + "/statMonth.do", timeout=60)
    now = now_kst()
    base = {"sltOrgType": "1", "sltOrgLvl1": "A", "sltOrgLvl2": "A", "gender": "gender", "genderPer": "genderPer",
            "generation": "generation", "sltUndefType": "", "sltOrderType": "1", "sltOrderValue": "ASC"}
    out = {}

    def read_csv(data, kind):
        r = req(s, "POST", f"{JUMIN}/downloadCsv.do?searchYearMonth={kind}&xlsStats=1", data=data, timeout=120)
        raw = r.content
        for enc in ("cp949", "utf-8-sig"):
            try:
                return list(csv.reader(io.StringIO(raw.decode(enc))))
            except UnicodeDecodeError:
                continue
        raise RuntimeError("인구 CSV 인코딩 오류")

    yr = read_csv(dict(base, category="year", searchYearMonth="year", searchYearStart=str(FIRST_YEAR), searchMonthStart="12",
                       searchYearEnd=str(now.year - 1), searchMonthEnd="12"), "year")
    head = yr[0]
    for row in yr[1:]:
        sd = short(row[0]) if "전국" not in row[0] else "전국"
        if not sd:
            continue
        for i, h in enumerate(head):
            m = re.match(r"(\d{4})년_총인구수", h)
            if m and i < len(row):
                v = num(row[i])
                if v:
                    out[(sd, int(m.group(1)))] = out.get((sd, int(m.group(1))), 0) + v
    # 가장 최근 달(이번 달 자료가 아직 없으면 지난달로)
    latest = None
    for back in range(0, 4):
        y, mth = now.year, now.month - back
        while mth <= 0:
            y, mth = y - 1, mth + 12
        try:
            rows = read_csv(dict(base, category="month", searchYearMonth="month", searchYearStart=str(y), searchMonthStart=f"{mth:02d}",
                                 searchYearEnd=str(y), searchMonthEnd=f"{mth:02d}"), "month")
        except Exception:  # noqa: BLE001
            continue
        col = next((i for i, h in enumerate(rows[0]) if h.endswith("총인구수")), None)
        if col is None or len(rows) < 3:
            continue
        got = {}
        for row in rows[1:]:
            sd = short(row[0]) if "전국" not in row[0] else "전국"
            if sd and col < len(row) and num(row[col]):
                got[sd] = got.get(sd, 0) + num(row[col])
        if got:
            for k, v in got.items():
                out[(k, "latest")] = v
            latest = f"{y}-{mth:02d}"
            break
    return out, latest


def collect_supply(s, status) -> None:
    now = now_kst()
    try:
        pl, meta, pl_units = plans(s)
        comp, comp_last = completions(s, now.strftime("%Y%m"))
        pop, pop_latest = population(s)
    except Exception as e:  # noqa: BLE001
        status["sources"]["입주 · 수요"] = {"ok": False, "error": str(e)[:300]}
        log("입주 · 수요 실패", e)
        return
    plan_from = meta["from"]
    last_year = int(meta["to"][:4])
    years = list(range(FIRST_YEAR, last_year + 1))
    regions = ["전국", "수도권", "지방"] + SIDO

    def members(rg):
        return SIDO if rg == "전국" else [x for x in SIDO if x in CAP] if rg == "수도권" else [x for x in SIDO if x not in CAP] if rg == "지방" else [rg]

    res = {}
    for rg in regions:
        mem = members(rg)
        act, pln, dem, pops = [], [], [], []
        for y in years:
            a = sum(v for (sd, ym), v in comp.items() if sd in mem and ym[:4] == str(y) and ym < plan_from)
            p = sum(v for (sd, ym), v in pl.items() if sd in mem and ym[:4] == str(y))
            has_a = any(sd in mem and ym[:4] == str(y) and ym < plan_from for (sd, ym) in comp)
            act.append(a if has_a else None)
            pln.append(p if p else None)
            if y < now.year:
                pv = pop.get(("전국", y)) if rg == "전국" else sum(pop.get((sd, y), 0) for sd in mem) or None
            else:
                pv = pop.get(("전국", "latest")) if rg == "전국" else sum(pop.get((sd, "latest"), 0) for sd in mem) or None
            pops.append(pv)
            dem.append(round(pv * DEMAND_RATE) if pv else None)
        res[rg] = {"actual": act, "plan": pln, "pop": pops, "demand": dem}
    partial = {}
    if meta["to"][4:] != "12":
        partial[str(last_year)] = int(meta["to"][4:])   # 마지막 해는 예정 자료가 이 달까지만 있음
    out = {"generated": now.strftime("%Y-%m-%d %H:%M"), "years": years, "regions": regions, "data": res,
           "actual_through": f"{comp_last[:4]}-{comp_last[4:]}" if comp_last else None,
           "plan": {"basis": meta["basis"], "from": f"{plan_from[:4]}-{plan_from[4:]}", "to": f"{meta['to'][:4]}-{meta['to'][4:]}",
                    "complexes": meta["complexes"]},
           "pop_latest": pop_latest, "demand_rate": DEMAND_RATE, "partial": partial}
    try:
        out["sgg"], out["sgg_meta"] = collect_sgg(s, years, plan_from, pl_units, now)
    except Exception as e:  # noqa: BLE001
        log("시군구 입주 · 수요 실패(직전 값 유지)", e)
        old = json.loads((DATA / "supply.json").read_text()) if (DATA / "supply.json").exists() else {}
        if old.get("sgg"):
            out["sgg"], out["sgg_meta"] = old["sgg"], dict(old.get("sgg_meta") or {}, stale=True)
        status["sources"]["시군구 입주"] = {"ok": False, "error": str(e)[:300]}
    else:
        status["sources"]["시군구 입주"] = {"ok": True, "kapt": out["sgg_meta"]["kapt_basis"], "units": sum(len(v) for v in out["sgg"].values())}
    (DATA / "supply.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    status["sources"]["입주 · 수요"] = {"ok": True, "actual_through": out["actual_through"], "plan_basis": meta["basis"], "pop": pop_latest}
    log("입주 · 수요 완료", out["actual_through"], meta, pop_latest)


def collect_sgg(s, years: list[int], plan_from: str, pl_units: dict, now) -> tuple[dict, dict]:
    """시도별 시 · 군 · 구 목록과 연도별 실적(K-apt) · 예정(청약홈) · 인구 · 수요"""
    from sgg import jumin_units, kapt_completions
    kc, kmeta = kapt_completions(s, short)
    jp, order, jlatest = jumin_units(s, short, FIRST_YEAR)
    idx_a, idx_p = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    for (sd, u, ym), v in kc.items():
        if ym < plan_from and ym[:4] >= str(FIRST_YEAR):
            idx_a[(sd, u)][int(ym[:4])] += v
    for (sd, u, ym), v in pl_units.items():
        idx_p[(sd, u)][int(ym[:4])] += v
    units = {(sd, u) for (sd, u, _) in jp}
    miss = sorted({k for k in list(idx_a) + list(idx_p) if k not in units})
    if miss:
        log("인구 자료에 없는 시군구(제외)", miss[:30])
    py = int(plan_from[:4])
    res = defaultdict(list)
    for sd, u in sorted(units, key=lambda k: (order.get(k, 99999), k[1])):
        act, pln, pops, dem = [], [], [], []
        for y in years:
            act.append(idx_a[(sd, u)].get(y, 0) if y <= py else None)
            pln.append(idx_p[(sd, u)].get(y, 0) if y >= py else None)
            pv = jp.get((sd, u, y)) if y < now.year else jp.get((sd, u, "latest"))
            pops.append(pv)
            dem.append(round(pv * DEMAND_RATE) if pv else None)
        if not any(pops):
            continue
        res[sd].append({"name": u, "actual": act, "plan": pln, "pop": pops, "demand": dem})
    meta = {"kapt_basis": kmeta["basis"], "kapt_complexes": kmeta["complexes"], "pop_latest": jlatest, "dropped": [f"{a} {b}" for a, b in miss]}
    log("시군구 입주 · 수요 완료", kmeta, jlatest, sum(len(v) for v in res.values()))
    return dict(res), meta
