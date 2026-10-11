"""지자체가 매월 공개하는 '업체별(단지별) 미분양 현황' 원문 파일을 받아 site/data/units/ 에 둔다.

국토교통부 미분양주택현황보고 양식(업체별 현황)을 시 · 도가 홈페이지에 그대로 올리는 경우가 많다.
단지별 총분양 가구수와 미분양 가구수(전월 · 당월)가 있어 분양률을 계산할 수 있다.
게시판 구조가 시 · 도마다 달라 시 · 도별로 '최신 게시물 → 첨부 파일 주소'를 찾는 함수를 둔다.
한 곳이 실패하면 그 곳은 직전 파일을 그대로 둔다. 원문은 바꾸지 않고 그대로 옮긴다.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Callable

from collect import DATA, log, now_kst, req

OUT = DATA / "units"
Q = urllib.parse.quote


def txt(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", x))).strip()


def month_of(title: str) -> str | None:
    t = re.sub(r"\s+", "", title)
    m = re.search(r"(20\d{2})년(\d{1,2})월", t) or re.search(r"(20\d{2})\.(\d{1,2})(?:\.|월)", t) \
        or re.search(r"(?<![\d.])'?(\d{2})\.(\d{1,2})(?:\.|월)", t)
    if not m:
        return None
    y = int(m.group(1)) + (2000 if len(m.group(1)) == 2 else 0)
    return f"{y}-{int(m.group(2)):02d}"


def date_after(page: str, label: str) -> str | None:
    m = re.search(r"(?:" + label + r").{0,120}?(20\d{2})[-.](\d{2})[-.](\d{2})", txt(page))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


FN = re.compile(r"[^<>\"'\n]{1,160}?\.(?:xlsx|xls|pdf|hwpx|hwp|zip)\b", re.I)


def pick(page: str, pat: str, prefer=("xlsx", "xls"), want: str | None = None) -> tuple[str, str]:
    """첨부 링크 pat(그룹 1 = 주소) 가운데 하나를 고른다. 링크마다 가장 가까이 적힌 파일 이름을 붙이고,
    이름에 want가 들어간 것 → prefer 확장자 순으로 고른다. (주소, 이름)"""
    names = [(m.start(), html.unescape(m.group(0)).strip()) for m in FN.finditer(page)
             if re.search(r"[가-힣]", m.group(0)) and "%" not in m.group(0)]
    cands = []
    for m in re.finditer(pat, page):
        nm = min(names, key=lambda x: x[0] - m.start() if x[0] >= m.start() else (m.start() - x[0]) * 3)[1] if names else ""
        cands.append((html.unescape(m.group(1)), nm))
    if not cands:
        raise RuntimeError("첨부 파일 링크를 찾지 못함")
    pool = [c for c in cands if want and want in c[1]] or cands
    for ext in prefer:
        for u, n in pool:
            if n.lower().endswith("." + ext):
                return u, n
    return pool[0]


# ── 시 · 도별 최신 게시물 찾기: dict(title, posted, post, url, name?, method?, data?) ──

def seoul(s):
    p = "https://land.seoul.go.kr/land/rent/rentCivilHouse.do"
    t = req(s, "GET", p).text
    m = re.search(r"downloadFileNm\('([^']+)'\s*,\s*'(unsold_(\d{8})\.[a-z]+)'\)", t)
    d = re.search(r"\[\s*(20\d{2})\.(\d{1,2})\.(\d{1,2})\s*기준\s*\]", txt(t))
    if not m or not d:
        raise RuntimeError("업체별 현황 링크를 찾지 못함")
    return dict(title=f"서울시 민간 미분양 주택 업체별 현황({d.group(1)}.{d.group(2)}.{d.group(3)} 기준)", post=p,
                posted=f"{m.group(3)[:4]}-{m.group(3)[4:6]}-{m.group(3)[6:]}",
                url=f"https://land.seoul.go.kr/land/common/downloadFileNm.do?filePath={Q(m.group(1), safe='')}&fileName={Q(m.group(2))}")


def busan(s):
    B = "https://www.busan.go.kr"
    t = req(s, "GET", B + "/depart/absalesinfo").text
    href, title = next((h, txt(x)) for h, x in re.findall(r'<a[^>]+href="(/depart/absalesinfo/\d+[^"]*)"[^>]*>(.*?)</a>', t, re.S)
                       if "미분양" in txt(x))
    v = req(s, "GET", B + html.unescape(href)).text
    u, n = pick(v, r'href="(/comm/getFile\?[^"]+)"')
    return dict(title=title, post=B + html.unescape(href).split("?")[0], posted=date_after(v, "작성일|등록일|게시일"), url=B + u, name=n)


DAEGU_BOARD = ("https://www.daegu.go.kr/build/index.do?menu_id=00001338&menu_link=/icms/bbs/selectBoardList.do&bbsId=BBS_00153"
               "&searchCnd=0&searchWrd=" + Q("공동주택 미분양"))


def daegu(s):
    B = "https://www.daegu.go.kr"
    t = req(s, "GET", DAEGU_BOARD, tries=2, timeout=25).text      # 해외 접속이 자주 막혀 오래 기다리지 않는다
    nid, title = next((i, txt(x)) for i, x in re.findall(r"fn_icms_navi_common\('view',\s*'(\d+)'\)[^>]*>(.*?)</a>", t, re.S)
                      if "미분양" in txt(x))
    post = f"{B}/build/index.do?menu_link=/icms/bbs/selectBoardArticle.do&menu_id=00001338&bbsId=BBS_00153&nttId={nid}"
    v = req(s, "GET", post).text
    u, n = pick(v, r"(fn_egov_downFile\('[^']+','\d+'\))")
    a, sn = re.search(r"'([^']+)','(\d+)'", u).groups()
    return dict(title=title, post=post, posted=date_after(v, "작성일|등록일"), name=n,
                url=f"{B}/icms/cmm/fms/FileDown.do?atchFileId={a}&fileSn={sn}")


def incheon(s):
    B = "https://www.incheon.go.kr"
    t = req(s, "GET", B + "/build/BU050301?srchKey=sj&srchText=" + Q("미분양 현황")).text
    href, title = next((h, txt(x)) for h, x in re.findall(r'<a[^>]+href="(/build/BU050301/\d+)"[^>]*>(.*?)</a>', t, re.S)
                       if "미분양" in txt(x))
    v = req(s, "GET", B + href).text
    u, n = pick(v, r'href="(/comm/getFile\?[^"]+)"')
    return dict(title=title, post=B + href, posted=date_after(v, "작성일|등록일|게시일"), url=B + u, name=n)


def daejeon(s):
    B = "https://www.daejeon.go.kr"
    t = req(s, "GET", B + "/urb/UrbNormalboardList.do?boardId=normal_0033&menuSeq=1246&searchCondition=title&searchKeyword="
            + Q("미분양")).text
    href, title = next((h, txt(x)) for h, x in re.findall(r'<a[^>]+href="(/urb/UrbNormalboardView\.do\?[^"]+)"[^>]*>(.*?)</a>', t, re.S)
                       if "미분양" in txt(x))
    post = B + html.unescape(href)
    v = req(s, "GET", post).text
    links = [(html.unescape(u), txt(n)) for u, n in re.findall(r'<a[^>]+href="(/cmm/Download\.do\?[^"]+)"[^>]*>(.*?)</a>', v, re.S)]
    u, n = next(((u, n) for u, n in links if "업체" in n), links[-1])
    return dict(title=title, post=post, posted=date_after(v, "작성일|등록일|게시일"), url=B + u, name=n)


def ulsan(s):
    B = "https://www.ulsan.go.kr/u/metro/bbs/"
    t = req(s, "GET", B + "list.ulsan?bbsId=BBS_0000000000000102&mId=001004013000000000").text
    href, title = next((h, txt(x)) for h, x in re.findall(r'<a[^>]+href="\./(view\.do\?[^"]+)"[^>]*>(.*?)</a>', t, re.S)
                       if "미분양" in txt(x))
    post = B + html.unescape(href)
    v = req(s, "GET", post).text
    m = re.search(r"EncDownFile\('/u','([^']+)','([^']+)','([^']+)'\)", v)
    n = re.search(r"EncDownFile[^>]+>(?:\s*<img[^>]*>)?\s*([^<]+?\.(?:xlsx|xls|pdf|hwp))", v)
    return dict(title=title, post=post, posted=date_after(v, "작성일자"), name=n.group(1).strip() if n else "",
                url=f"https://www.ulsan.go.kr/u/enc/media/bbsFileDown.do?bbsId={m.group(1)}&atchFileId={Q(m.group(2), safe='')}"
                    f"&fileSn={Q(m.group(3), safe='')}")


def gyeonggi(s):
    r = req(s, "POST", "https://www.gg.go.kr/ajax/board/getList.do",
            data={"bsIdx": "551", "bcIdx": "0", "menuId": "1799", "isManager": "false", "isCharge": "false",
                  "offset": "0", "limit": "5"},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": "https://www.gg.go.kr/bbs/board.do?bsIdx=551&menuId=1799"})
    it = next(x for x in r.json()["items"] if "미분양" in x.get("SUBJECT", "") and x.get("IMAGE_IDX"))
    return dict(title=it["SUBJECT"], posted=it.get("WRITE_DATE2"), name=it.get("IMAGE_STR"),
                post=f"https://www.gg.go.kr/bbs/boardView.do?bIdx={it['B_IDX']}&bsIdx=551&bcIdx=0&menuId=1799",
                url=f"https://www.gg.go.kr/cmmn/download.do?idx={it['IMAGE_IDX']}")


def gangwon(s):
    B = "https://state.gwd.go.kr"
    t = req(s, "GET", B + "/portal/bulletin/notice?searchCondition=TITLE&recordCountPerPage=15&searchKeyword="
            + Q("미분양 주택 현황")).text
    for row in re.split(r"(?i)<tr[ >]", t):
        m = re.search(r"goPage\((\d+)\)[^>]*>(.*?)</a>", row, re.S)
        if not m or "미분양" not in txt(m.group(2)):
            continue
        f = re.search(r'href="(/egf/bp/common/front/\d+/download)"[^>]*>(.*?)</a>', row, re.S)
        if not f:
            continue
        d = re.search(r"(20\d{2}-\d{2}-\d{2})", row)
        return dict(title=txt(m.group(2)), post=f"{B}/portal/bulletin/notice?articleSeq={m.group(1)}",
                    posted=d.group(1) if d else None, url=B + f.group(1), name=txt(f.group(2)))
    raise RuntimeError("미분양 게시물을 찾지 못함")


def chungbuk(s):
    B = "https://www.chungbuk.go.kr/www/"
    t = req(s, "GET", B + "selectBbsNttList.do?bbsNo=97&key=646").text
    href, title = next((h, txt(x)) for h, x in re.findall(r'<a[^>]+href="\./(selectBbsNttView\.do\?[^"]+)"[^>]*>(.*?)</a>', t, re.S)
                       if "미분양" in txt(x))
    post = B + html.unescape(href)
    v = req(s, "GET", post).text
    u, n = pick(v, r'href="(/www/downloadBbsFile\.do\?[^"]+)"', prefer=("xlsx", "xls", "pdf"), want="업체")
    return dict(title=title, post=post, posted=date_after(v, "작성일|등록일|게시일"), url="https://www.chungbuk.go.kr" + u, name=n)


def chungnam(s):
    B = "https://www.chungnam.go.kr"
    t = req(s, "GET", B + "/cnportal/bbs/B0000134/list.do?menuNo=500176&searchCnd=1&searchWrd=" + Q("미분양")).text
    href, title = next((h, txt(x)) for h, x in re.findall(r'<a[^>]+href="(/cnportal/bbs/B0000134/view\.do\?[^"]+)"[^>]*>(.*?)</a>', t, re.S)
                       if "미분양" in txt(x))
    post = B + html.unescape(href)
    v = req(s, "GET", post).text
    u, n = pick(v, r'href="(/cnportal/cmmn/file/fileDown\.do\?[^"]+)"')
    return dict(title=title, post=post, posted=date_after(v, "작성일|등록일|게시일"), url=B + u, name=n)


def gyeongbuk(s):
    B = "https://www.gb.go.kr/Main/finace/"
    t = req(s, "GET", B + "page.do?mnu_uid=15434").text
    href, title = next((h, txt(x)) for h, x in re.findall(r'<a[^>]+href="\./(page\.do\?mnu_uid=15434[^"]*B_NUM=[^"]+)"[^>]*>(.*?)</a>', t, re.S)
                       if "미분양" in txt(x))
    post = B + html.unescape(href)
    v = req(s, "GET", post).text
    u, n = pick(v, r'href="(/Common/board_egov/download\.jsp\?[^"]+)"')
    return dict(title=title, post=post, posted=date_after(v, "작성일|등록일|게시일"), url="https://www.gb.go.kr" + u, name=n)


def gyeongnam(s):
    B = "https://gis.gyeongnam.go.kr/srp/"
    r = req(s, "POST", B + "moduleRetrivePagingMapper.do",
            data={"gubun": "us_title", "search": "", "sqlId": "unsold.selectUnsoldList", "sqlIdListCnt": "unsold.selectUnsoldCnt",
                  "pageIndex": "1", "pageunit": "5"},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": B + "unsoldAptStat.do"})
    it = next(x for x in r.json()["list"] if x.get("us_file_new_nm") and x.get("us_file_del_yn", "N") != "Y")
    return dict(title=it["us_title"], posted=it.get("first_register_dt"), post=B + "unsoldAptStat.do", name=it.get("us_file_ori_nm"),
                url=f"{B}unsold/{it['us_id']}/{it['us_file_new_nm']}{it.get('us_file_ext') or ''}")


# 화면 순서(시 · 도 표준 순서). code는 파일 이름에 쓴다.
SOURCES: list[tuple[str, str, str, Callable, str]] = [
    ("seoul", "서울", "서울특별시(서울부동산정보광장)", seoul, "https://land.seoul.go.kr/land/rent/rentCivilHouse.do"),
    ("busan", "부산", "부산광역시", busan, "https://www.busan.go.kr/depart/absalesinfo"),
    ("daegu", "대구", "대구광역시", daegu, DAEGU_BOARD),
    ("incheon", "인천", "인천광역시", incheon, "https://www.incheon.go.kr/build/BU050301?srchKey=sj&srchText=" + Q("미분양 현황")),
    ("daejeon", "대전", "대전광역시", daejeon,
     "https://www.daejeon.go.kr/urb/UrbNormalboardList.do?boardId=normal_0033&menuSeq=1246&searchCondition=title&searchKeyword=" + Q("미분양")),
    ("ulsan", "울산", "울산광역시", ulsan, "https://www.ulsan.go.kr/u/metro/bbs/list.ulsan?bbsId=BBS_0000000000000102&mId=001004013000000000"),
    ("gyeonggi", "경기", "경기도", gyeonggi, "https://www.gg.go.kr/bbs/board.do?bsIdx=551&menuId=1799"),
    ("gangwon", "강원", "강원특별자치도", gangwon,
     "https://state.gwd.go.kr/portal/bulletin/notice?searchCondition=TITLE&searchKeyword=" + Q("미분양 주택 현황")),
    ("chungbuk", "충북", "충청북도", chungbuk, "https://www.chungbuk.go.kr/www/selectBbsNttList.do?bbsNo=97&key=646"),
    ("chungnam", "충남", "충청남도", chungnam,
     "https://www.chungnam.go.kr/cnportal/bbs/B0000134/list.do?menuNo=500176&searchCnd=1&searchWrd=" + Q("미분양")),
    ("gyeongbuk", "경북", "경상북도", gyeongbuk, "https://www.gb.go.kr/Main/finace/page.do?mnu_uid=15434"),
    ("gyeongnam", "경남", "경상남도(경남 스마트 부동산 포털)", gyeongnam, "https://gis.gyeongnam.go.kr/srp/unsoldAptStat.do"),
]
# 해외 서버(GitHub Actions)에서 접속이 막힌 곳: 화면에서 게시판으로 바로 보낸다
BLOCKED = {"daegu": "대구시 홈페이지가 해외 서버 접속을 자주 막아 아직 받지 못했습니다"}
NOT_PUBLISHED = ["광주 · 전남", "전북", "세종", "제주"]


def cd_name(r) -> str:
    cd = r.headers.get("content-disposition") or ""
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd, re.I)
    if m:
        return urllib.parse.unquote(m.group(1)).strip('"')
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
    if not m:
        return ""
    raw = m.group(1)
    try:
        b = raw.encode("latin-1")
        for enc in ("utf-8", "cp949"):
            try:
                raw = b.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    except UnicodeEncodeError:
        pass
    return urllib.parse.unquote(raw).replace("+", " ").strip()


def kind(head: bytes, name: str) -> str | None:
    n = name.lower()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        return "hwpx" if n.endswith(".hwpx") else "zip" if n.endswith(".zip") else "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "hwp" if n.endswith(".hwp") else "xls"
    return None


def collect_units(s, status) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    f = DATA / "units.json"
    old = {x["code"]: x for x in json.loads(f.read_text()).get("items", [])} if f.exists() else {}
    now = now_kst()
    items, ok_n = [], 0
    for code, rg, org, fn, board in SOURCES:
        prev = old.get(code)
        try:
            p = fn(s)
            key = p["title"] + "|" + p["url"]
            if prev and prev.get("key") == key and prev.get("file") and (OUT / prev["file"].split("/")[-1]).exists():
                items.append(dict(prev, board=board, checked=now.strftime("%Y-%m-%d"), ok=True, error=None))
                ok_n += 1
                continue
            r = req(s, "GET", p["url"], timeout=120, headers={"Referer": p["post"]})
            body, name = r.content, cd_name(r)
            if not re.search(r"[가-힣]", name) and p.get("name"):     # 저장 이름이 일련번호뿐이면 게시판에 적힌 이름을 쓴다
                name = p["name"]
            ext = kind(body[:8], name)
            if not ext or len(body) < 2000:
                raise RuntimeError(f"파일이 아닌 응답({r.headers.get('content-type')}, {len(body)}B)")
            for old_f in OUT.glob(code + ".*"):
                old_f.unlink()
            (OUT / f"{code}.{ext}").write_bytes(body)
            items.append(dict(code=code, rg=rg, org=org, title=p["title"], month=month_of(p["title"]) or month_of(name),
                              posted=p.get("posted"), post=p["post"], board=board, file=f"data/units/{code}.{ext}", ext=ext, size=len(body),
                              orig=name, key=key, fetched=now.strftime("%Y-%m-%d"), checked=now.strftime("%Y-%m-%d"),
                              ok=True, error=None))
            ok_n += 1
            log("단지별 미분양", rg, p["title"], ext, len(body))
        except Exception as e:  # noqa: BLE001
            log("단지별 미분양 실패", rg, repr(e)[:200])
            base = prev or dict(code=code, rg=rg, org=org)
            items.append(dict(base, board=board, ok=False, error=str(e)[:200], note=BLOCKED.get(code),
                              checked=now.strftime("%Y-%m-%d")))
    f.write_text(json.dumps({"generated": now.strftime("%Y-%m-%d %H:%M"), "items": items, "not_published": NOT_PUBLISHED},
                            ensure_ascii=False, indent=1))
    status["sources"]["단지별 미분양(지자체)"] = {"ok": ok_n > 0, "regions": ok_n, "of": len(SOURCES),
                                            "failed": [x["rg"] for x in items if not x.get("ok") and x["code"] not in BLOCKED]}
