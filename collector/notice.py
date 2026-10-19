"""입주자모집공고문(PDF)에서 발코니 확장비와 중도금 대출 조건을 읽는다.

청약홈 상세 화면에는 없고 공고문에만 있는 항목이다. 공고문 양식이 단지마다 달라
다음처럼 보수적으로 읽고, 확실하지 않으면 비워 둔다.
- 발코니 확장비: '발코니 확장 공사비/금액/공급금액' 표의 행(약식표기 · 확장금액 · 계약금 …)에서
  계약금이 확장금액의 4~35%로 맞아떨어지는 행만 쓴다. 표가 없고 '무상 제공 · 분양가 포함' 문구가
  있으면 무상으로 본다.
- 중도금 대출: '무이자' · '이자후불제' · '이자 자납'(계약자 부담) 가운데 공고문이 본 단지 조건으로
  적은 것. 부정 · 가정 문장(…적용되지 아니하며, …불가 시, 또는 …에 준하는)은 세지 않는다.
"""
from __future__ import annotations

import re

AMT = r"\d{1,3}(?:,\d{3}){1,2}"
# 59A · 84 · 84A/B · 84B1 · 147P · PH184B · 182PH · 059.9986A · 63A, 63B
TY1 = r"(?:PH|P)?\d{2,3}(?:\.\d{1,4})?[A-Z]{0,2}\d?(?:-\d)?(?:/[A-Z]\d?)*(?:PH|P|T)?"
TYS = rf"{TY1}(?:\s*,\s*{TY1})*"
ROW = re.compile(rf"(?<![\w.,\-])({TYS})\s+({AMT})\s+({AMT})(?![\d,])")
BAL_HEAD = re.compile(r"발코니\s*확장")
ALL_ROW = re.compile(rf"(?:전\s*세대|전\s*타입|전체\s*타입|공통)[^\d■]{{0,70}}?({AMT})\s+({AMT})(?![\d,])")
STOP = re.compile(r"에어컨|추가\s*선택\s*품목|\(\d\)\s*\S{0,6}특화|특화\s*\[|시스템\s*가구|유상\s*옵션\s*[\(\[]|빌트인|중문|붙박이|아트월|신발장|마이너스\s*옵션|마루|바닥\s*재|플러스\s*옵션|Style\s*Up")
NOT = r"(?!\s*되?\s*(?:지|어\s*있지|아니))"
FREE = re.compile(r"발코니\s*확장\s*(?:을|를|은|이|공사비?용?\s*(?:을|를|은|는)?)?\s*(?:전\s*세대\s*)?무상\s*(?:으로\s*)?(?:제공|시공|확장)"
                  r"|발코니\s*확장\s*\(\s*무상"
                  r"|발코니\s*확장\s*(?:금액|공사비|비용)\s*무상"
                  r"|발코니\s*확장\s*(?:은|이|금액은|공사비는|비용은|공사\s*비용은?)?[^.■]{0,40}?무상으로\s*제공(?!\s*(?:되는\s*)?품목)"
                  rf"|발코니\s*확장(?:은|이)?[^.。■]{{0,30}}?분양\s*(?:가|금액|가격)\S{{0,3}}\s*포함{NOT}"
                  rf"|공급\s*(?:금액|가격|가)(?:에는|은|에)\s*발코니\s*확장\s*(?:공사\s*)?(?:비용|비)?\s*(?:이|가)?\s*포함{NOT}")


def text_of(pdf: bytes, maxpages: int = 80) -> str:
    """PDF 글자를 페이지 순서대로 뽑는다(pdfium: 표가 행 단위로 나와 읽기 쉽고 빠르다)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    out = []
    try:
        for i in range(min(len(doc), maxpages)):
            pg = doc[i]
            tp = pg.get_textpage()
            out.append(tp.get_text_range())
            tp.close(); pg.close()
    finally:
        doc.close()
    return "\n".join(out)


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def _area(ty: str) -> int | None:
    m = re.search(r"(\d{2,3})(?:\.\d+)?", ty)
    return int(m.group(1)) if m else None


TY_TOK = re.compile(rf"^{TY1},?$")
AMT_TOK = re.compile(rf"^{AMT}$")


def _horizontal(win: str) -> dict[str, int]:
    """'약식표기 59A 84A 84B 발코니 확장 금액 11,700,000 12,800,000 16,600,000 계약금(10%) 1,170,000 …'처럼
    표가 가로로 읽힌 경우. 타입 수만큼 금액이 이어지고, 다음 줄(계약금)이 그 4~35%로 맞거나
    금액이 정확히 타입 수만큼일 때만 쓴다."""
    tk = win.split(" ")
    out: dict[str, int] = {}
    i = 0
    while i < len(tk):
        j = i
        while j < len(tk) and TY_TOK.match(tk[j]) and 20 <= (_area(tk[j]) or 0) <= 330:
            j += 1
        if j - i < 2:
            i += 1; continue
        tys = [t.rstrip(",") for t in tk[i:j]]
        k, lab = j, []
        while k < len(tk) and k - j < 12 and not AMT_TOK.match(tk[k]) and not TY_TOK.match(tk[k]):
            lab.append(tk[k]); k += 1
        a0 = k
        while k < len(tk) and AMT_TOK.match(tk[k]):
            k += 1
        amts = [_num(x) for x in tk[a0:k]]
        if len(amts) == len(tys) and all(1_000_000 <= a <= 90_000_000 for a in amts):   # 수가 다르면(층별 금액 등) 짝을 알 수 없어 버린다
            m2, nxt = k, []
            while m2 < len(tk) and m2 - k < 8 and not AMT_TOK.match(tk[m2]):
                m2 += 1
            while m2 < len(tk) and AMT_TOK.match(tk[m2]):
                nxt.append(_num(tk[m2])); m2 += 1
            good = False
            if len(nxt) >= len(tys):
                good = sum(0.04 <= b / a <= 0.35 for a, b in zip(amts, nxt)) >= max(1, len(tys) // 2)
            elif re.search(r"금액|공사비|대금|공급|확장", " ".join(lab)):
                good = True
            if good:
                for t, a in zip(tys, amts):
                    out.setdefault(t, a)
                i = k; continue
        i = j
    return out


def balcony(text: str) -> tuple[dict | None, str | None]:
    """{약식표기: 확장금액(원)}, 또는 무상이면 (None, '무상'). 못 읽으면 (None, None)."""
    s = re.sub(r"\s+", " ", text)
    ok = lambda a, b: 1_000_000 <= a <= 90_000_000 and 0.04 <= b / a <= 0.35  # noqa: E731
    best: dict[str, int] = {}
    p0 = None                                # 확장비 표가 처음 나온 곳. 표가 쪽을 넘겨 이어져도 그 근처만 본다
    for m in BAL_HEAD.finditer(s):
        if p0 is not None and m.start() > p0 + 3500:
            break
        if "에어컨" in s[m.start():m.end() + 40]:
            continue
        win = s[m.start():m.start() + 1600]
        st = STOP.search(win, 60)
        if st:
            win = win[:st.start()]
        got = False
        for r in ROW.finditer(win):          # 표의 한 행: 약식표기 · 확장금액 · 계약금 …
            a, b = _num(r.group(2)), _num(r.group(3))
            if not ok(a, b):
                continue
            for ty in re.split(r"\s*,\s*", r.group(1)):
                ar = _area(ty)
                if ar and 20 <= ar <= 330:
                    got = True
                    best.setdefault(ty, a)
        for ty, a in _horizontal(win).items():
            got = True
            best.setdefault(ty, a)
        if not best:                         # '전세대 23,100,000 …'처럼 타입 구분 없이 한 값
            r = ALL_ROW.search(win)
            if r and ok(_num(r.group(1)), _num(r.group(2))):
                best["전체"] = _num(r.group(1)); got = True
        if got and p0 is None:
            p0 = m.start()
    if best:
        v = sorted(best.values()); med = v[len(v) // 2]
        best = {k: a for k, a in best.items() if med * 0.3 <= a <= med * 3}   # 옵션 품목 행이 섞이면 버린다
        return best, None
    if FREE.search(s):
        return None, "무상"
    return None, None


MID = {
    "무이자": re.compile(r"중도금\s*(?:대출\s*)?(?:이자\s*)?(?:\(\s*)?무\s*이자|무이자\s*(?:조건|대출|융자)"),
    "후불제": re.compile(r"이자\s*후\s*불\s*제?|후불\s*이자"),
    "자납": re.compile(r"이자\s*(?:자납|자부담)|자납\s*조건"),
}
NEG_AFTER = re.compile(r"^.{0,22}?(?:적용\s*되?\s*지\s*(?:아니|않)|아니(?:하|며|함)|않(?:으|습|는)|불가|에\s*준하|또는|대신)")
NEG_BEFORE = re.compile(r"(?:또는|않|아니|불가|경우에도|대신하여|이\s*아닌|외의)[^.]{0,12}$")


def midloan(text: str) -> tuple[str | None, dict]:
    """중도금 대출 조건: '무이자' · '후불제' · '자납' 또는 None. 두 번째 값은 판정 근거(종류별 횟수)."""
    s = re.sub(r"[“”‘’'\"「」『』]", "", re.sub(r"\s+", " ", text))
    cnt = {}
    for k, rx in MID.items():
        n = 0
        for m in rx.finditer(s):
            before, after = s[max(0, m.start() - 26):m.start()], s[m.end():m.end() + 40]
            if NEG_AFTER.search(after) or NEG_BEFORE.search(before):
                continue
            n += 1
        if n:
            cnt[k] = n
    if not cnt:
        return None, cnt
    # 일부 회차만 무이자(나머지 후불제)인 경우가 있어, 둘 다 나오면 '무이자'라고 단정하지 않는다
    if "무이자" in cnt and "후불제" in cnt:
        part = re.search(r"\d\s*(?:~|∼|-|～)\s*\d\s*(?:회|차)[^.]{0,20}무이자|무이자[^.]{0,30}\d\s*(?:~|∼|-|～)\s*\d\s*(?:회|차)", s)
        if part:
            return "일부 무이자", cnt
        return ("무이자" if cnt["무이자"] > cnt["후불제"] else "후불제"), cnt
    if "자납" in cnt and len(cnt) > 1:
        cnt2 = {k: v for k, v in cnt.items() if k != "자납"}
        return max(cnt2, key=cnt2.get), cnt
    return max(cnt, key=cnt.get), cnt


def parse(text: str) -> dict:
    rows, free = balcony(text)
    mid, why = midloan(text)
    return {"bal": rows, "bal_free": free == "무상", "mid": mid, "mid_n": why}


# ───────────────────────── 수집 · 표에 붙이기 ─────────────────────────
PARSER_V = 1                                 # 읽는 규칙을 고치면 올린다 → 다음 수집부터 차례로 다시 읽음
RETRY_DAYS = 7                               # 공고문 링크가 없거나 못 받은 단지는 일주일 뒤 다시 시도
LBL = re.compile(r"^(?:PH|P)?0*(\d{2,3})(?:\.\d+)?([A-Z]{0,2}\d?)(?:-\d)?((?:/[A-Z]\d?)*)(?:PH|P|T)?$")


def _keys(label: str) -> list[tuple[int, str]]:
    """'84A/B' → [(84,'A'),(84,'B')], '84' → [(84,'*')], '059.9986A' → [(59,'A')]"""
    m = LBL.match(label)
    if not m:
        return []
    ar, suf, alt = int(m.group(1)), m.group(2), m.group(3)
    out = [(ar, suf or "*")]
    out += [(ar, a) for a in alt.split("/") if a]
    return out


def _tykey(ty: str) -> tuple[int, str] | None:
    m = re.match(r"^0*(\d{2,3})(?:\.\d+)?([A-Z]{0,2}\d?)", ty or "")
    return (int(m.group(1)), m.group(2) or "*") if m else None


def attach(rows: list[dict], cache: dict) -> None:
    """캐시(cache/notice.json)의 공고문 판독 결과를 청약 표 행에 붙인다(네트워크 없음)."""
    for r in rows:
        c = cache.get(r["pb"])
        if not c or "x" in c:
            continue
        r["mid"] = c.get("m")
        if c.get("f"):
            r["balf"] = True
        b = c.get("b") or {}
        if not b:
            continue
        allv = b.get("전체")
        tab: dict[tuple[int, str], int] = {}
        for lab, a in b.items():
            for k in _keys(lab):
                tab.setdefault(k, a)
        b84 = [a for (ar, _), a in tab.items() if ar == 84]
        for t in r.get("types") or []:
            k = _tykey(t.get("ty"))
            if not k:
                continue
            v = allv or tab.get(k) or tab.get((k[0], "*")) or tab.get((k[0], k[1][:1] or "*"))
            if v is None:
                same = [a for (ar, _), a in tab.items() if ar == k[0]]
                if len(set(same)) == 1:
                    v = same[0]
            if v:
                t["bal"] = v
        if allv and any(_tykey(t.get("ty")) and _tykey(t.get("ty"))[0] == 84 for t in r.get("types") or []):
            r["bal84"] = allv
        elif b84 and any((_tykey(t.get("ty")) or (0,))[0] == 84 for t in r.get("types") or []):
            r["bal84"] = max(b84)


def notice_pdf(s, req, hm: str, pb: str) -> tuple[bytes | None, str]:
    from collect import AH
    page = req(s, "GET", f"{AH}/ai/aia/selectAPTLttotPblancDetail.do?houseManageNo={hm}&pblancNo={pb}", timeout=40, tries=2).text
    links = re.findall(r'(?s)<a[^>]+href="([^"]*getAtchmnfl\.do[^"]*)"[^>]*>(.*?)</a>', page)
    url = next((u for u, t in links if "모집공고" in re.sub(r"<[^>]+>", "", t)), links[0][0] if links else None)
    if not url:
        return None, "청약홈에 공고문 없음"
    url = url.replace("&amp;", "&")
    if url.startswith("/"):
        url = "https://static.applyhome.co.kr" + url
    r = req(s, "GET", url, timeout=90, tries=2)
    if not r.content.startswith(b"%PDF"):
        return None, "PDF 아님"
    return r.content, "ok"


def collect_notice(s, status, log, budget_s: int | None = None, max_n: int = 400) -> None:
    """청약 표(site/data/subscriptions.json)의 단지 가운데 아직 공고문을 읽지 않은 곳을 새 공고부터 읽는다."""
    import datetime as dt
    import json
    import time

    from collect import CACHE, DATA, now_kst, req
    import os
    budget_s = budget_s or int(os.environ.get("NOTICE_BUDGET", "330"))
    sub_f, cache_f = DATA / "subscriptions.json", CACHE / "notice.json"
    sub = json.loads(sub_f.read_text())
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    today = now_kst().date()
    t0, n, got, miss = time.time(), 0, 0, 0

    def save():
        keep = {r["pb"] for r in sub["rows"]}
        CACHE.mkdir(exist_ok=True)
        cache_f.write_text(json.dumps({k: v for k, v in cache.items() if k in keep}, ensure_ascii=False, separators=(",", ":")))

    for r in sub["rows"]:
        if time.time() - t0 > budget_s or n >= max_n:
            break
        c = cache.get(r["pb"])
        if c and c.get("v") == PARSER_V and "x" not in c:
            continue
        if c and "x" in c:
            try:
                if (today - dt.date.fromisoformat(c.get("d", "2000-01-01"))).days < RETRY_DAYS:
                    continue
            except ValueError:
                pass
        n += 1
        try:
            pdf, why = notice_pdf(s, req, r["hm"], r["pb"])
            if not pdf:
                cache[r["pb"]] = {"v": PARSER_V, "x": why, "d": today.isoformat()}; miss += 1
                continue
            p = parse(text_of(pdf))
            cache[r["pb"]] = {"v": PARSER_V, "b": p["bal"] or {}, "f": p["bal_free"], "m": p["mid"], "d": today.isoformat()}
            got += 1
        except Exception as e:  # noqa: BLE001
            cache[r["pb"]] = {"v": PARSER_V, "x": f"읽기 실패 {str(e)[:80]}", "d": today.isoformat()}; miss += 1
            log("공고문 읽기 실패", r["name"], e)
        if n % 20 == 0:
            save(); log(f"  공고문 {n}건 (읽음 {got}, 없음 · 실패 {miss}, {time.time() - t0:.0f}초)")
        time.sleep(0.3)
    save()
    attach(sub["rows"], cache)
    sub_f.write_text(json.dumps(sub, ensure_ascii=False, separators=(",", ":")))
    rows = sub["rows"]
    done = sum(1 for r in rows if (cache.get(r["pb"]) or {}).get("v") == PARSER_V and "x" not in cache[r["pb"]])
    status["sources"]["모집공고문"] = {"ok": True, "read_now": got, "miss_now": miss, "done": done, "rows": len(rows),
                                   "bal": sum(1 for r in rows if r.get("bal84") or r.get("balf")),
                                   "mid": sum(1 for r in rows if r.get("mid"))}
    log("모집공고문 완료: 이번", got, "건 읽음 · 누적", done, "/", len(rows))
