"""아파트 분양시장 청약 · 미분양 현황 수집기.

1) 청약홈(applyhome.co.kr) APT 분양정보 목록 → 단지별 상세(공급위치·주택형·공급금액·시행/시공사)
   와 청약 접수 경쟁률(주택형·순위·지역별 접수건수, 청약결과)을 받는다.
2) 국토교통 통계누리 미분양주택현황보고 → 시·군·구별 미분양(formId 2082)과
   시·군·구별 공사완료후 미분양(formId 5328)을 월별로 받는다.

결과는 site/data/*.json, 단지별 원자료 캐시는 cache/applyhome.json에 둔다.
한 출처가 실패하면 직전 값을 유지하고 status.json에 실패로 남긴다.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data"
CACHE = ROOT / "cache"
AH = "https://www.applyhome.co.kr"
MOLIT = "https://stat.molit.go.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MONTHS_BACK = int(os.environ.get("AH_MONTHS", "6"))      # 모집공고 기준 최근 N개월
UNSOLD_MONTHS = int(os.environ.get("UNSOLD_MONTHS", "13"))
PY = 3.305785


def log(*a):
    print(dt.datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


def now_kst():
    return dt.datetime.now(KST)


def sess():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    return s


def req(s, method, url, tries=3, timeout=60, **kw):
    last = None
    for i in range(tries):
        try:
            r = s.request(method, url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise last  # type: ignore[misc]


def clean(x: str) -> str:
    x = re.sub(r"(?s)<br\s*/?>", " ", x)
    x = re.sub(r"(?s)<[^>]+>", " ", x)
    return re.sub(r"\s+", " ", html.unescape(x)).strip()


def cells(tr: str) -> list[str]:
    return [clean(c) for c in re.findall(r"(?s)<t[dh][^>]*>(.*?)</t[dh]>", tr)]


def tables(page: str) -> dict[str, list[list[str]]]:
    out = {}
    for t in re.findall(r"(?s)<table.*?</table>", page):
        m = re.search(r"(?s)<caption[^>]*>(.*?)</caption>", t)
        cap = clean(m.group(1)) if m else f"t{len(out)}"
        out[cap] = [cells(tr) for tr in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", t)]
    return out


def num(x):
    if x is None:
        return None
    x = str(x).replace(",", "").strip()
    try:
        return float(x) if "." in x else int(x)
    except ValueError:
        return None


# ───────────────────────── 청약홈 ─────────────────────────
TY = re.compile(r"^\d{3}\.\d+\s*[A-Z0-9]*$")


def ah_list(s) -> list[dict]:
    end = now_kst().date().replace(day=1)
    y, m = end.year, end.month - (MONTHS_BACK - 1)
    while m <= 0:
        y, m = y - 1, m + 12
    begin = f"{y}{m:02d}"
    rows, page = [], 1
    while page < 80:
        r = req(s, "POST", AH + "/ai/aia/selectAPTLttotPblancListView.do",
                data={"beginPd": begin, "endPd": end.strftime("%Y%m"), "pageIndex": str(page)})
        trs = re.findall(r'(?s)<tr data-pbno="(\d+)" data-hmno="(\d+)" data-honm="([^"]*)">(.*?)</tr>', r.text)
        if not trs:
            break
        for pb, hm, nm, body in trs:
            c = cells(body)
            if len(c) < 9:
                continue
            rows.append({"pb": pb, "hm": hm, "name": html.unescape(nm).strip(), "sido": c[0], "kind": c[1],
                         "sale": c[2], "builder": c[4], "notice": c[6], "period": c[7], "winner": c[8]})
        nxt = re.search(rf"pageIndex={page + 1}\b", r.text)
        if not nxt:
            break
        page += 1
        time.sleep(0.4)
    # 같은 공고가 여러 페이지에 걸쳐 중복되면 제거
    seen, out = set(), []
    for x in rows:
        if x["pb"] in seen:
            continue
        seen.add(x["pb"]); out.append(x)
    log("청약홈 목록", begin, "~", end.strftime("%Y%m"), len(out), "건")
    return out


def ah_detail(s, hm, pb) -> dict:
    r = req(s, "GET", f"{AH}/ai/aia/selectAPTLttotPblancDetail.do?houseManageNo={hm}&pblancNo={pb}")
    t = tables(r.text)
    d = {"addr": None, "units": None, "types": [], "developer": None, "constructor": None, "movein": None, "contract": None}
    for cap, rows in t.items():
        if "주요정보" in cap:
            for c in rows:
                if len(c) >= 2 and c[0] == "공급위치":
                    d["addr"] = c[1]
                if len(c) >= 2 and c[0] == "공급규모":
                    d["units"] = num(re.sub(r"[^\d,]", "", c[1]) or None)
        elif "청약일정" in cap:
            for c in rows:
                if c and c[0] == "계약일" and len(c) > 1:
                    d["contract"] = c[1]
        elif "특별공급" in cap:
            continue
        elif "공급대상" in cap:
            for c in rows:
                idx = next((i for i, v in enumerate(c) if TY.match(v)), None)
                if idx is None or len(c) < idx + 5:
                    continue
                d["types"].append({"ty": c[idx].replace(" ", ""), "area": num(c[idx + 1]), "gen": num(c[idx + 2]),
                                   "spc": num(c[idx + 3]), "tot": num(c[idx + 4])})
        elif "공급금액" in cap:
            prices = {}
            for c in rows:
                if len(c) >= 2 and TY.match(c[0]):
                    prices[c[0].replace(" ", "")] = num(c[1])
            for x in d["types"]:
                x["price"] = prices.get(x["ty"])
        elif "기타사항" in cap:
            for c in rows:
                if len(c) >= 2 and c[0] not in ("시행사",):
                    d["developer"], d["constructor"] = c[0], c[1]
    m = re.search(r"입주예정월\s*:\s*([\d.]+)", clean(r.text))
    d["movein"] = m.group(1) if m else None
    return d


def ah_competition(s, hm, pb) -> dict:
    r = req(s, "GET", f"{AH}/ai/aia/selectAPTCompetitionPopup.do?houseManageNo={hm}&pblancNo={pb}")
    by = {}
    for tr in re.findall(r'(?s)<tr data-ty="[^"]*"[^>]*>(.*?)</tr>', r.text):
        c = cells(tr)
        if len(c) < 7:
            continue
        ty, sup, rank, area, cnt, rate, res = c[0].replace(" ", ""), num(c[1]), c[2], c[3], num(c[4]), c[5], c[6]
        x = by.setdefault(ty, {"ty": ty, "sup": sup, "r1": 0, "r2": 0, "r1loc": 0, "result": res, "short": None, "score": None})
        if rank.startswith("1"):
            x["r1"] += cnt or 0
            if area == "해당지역":
                x["r1loc"] += cnt or 0
        else:
            x["r2"] += cnt or 0
        m = re.search(r"△\s*([\d,]+)", rate)
        if m:
            x["short"] = max(x["short"] or 0, num(m.group(1)) or 0)
        if len(c) >= 11 and area == "해당지역" and rank.startswith("1") and num(c[10]) is not None:
            x["score"] = {"min": num(c[8]), "max": num(c[9]), "avg": num(c[10])}
    return {"types": list(by.values())}


def summarize(base: dict, det: dict, cmp_: dict | None) -> dict:
    types = det.get("types") or []
    cm = {x["ty"]: x for x in (cmp_ or {}).get("types", [])}
    rows, w_amt, w_area = [], 0.0, 0.0
    for t in types:
        c = cm.get(t["ty"], {})
        ppy = round(t["price"] / (t["area"] / PY)) if t.get("price") and t.get("area") else None
        rows.append({"ty": t["ty"], "area": t.get("area"), "gen": t.get("gen"), "spc": t.get("spc"), "tot": t.get("tot"),
                     "price": t.get("price"), "ppy": ppy, "sup": c.get("sup"), "r1": c.get("r1"), "r2": c.get("r2"),
                     "rate": round(c["r1"] / c["sup"], 2) if c.get("sup") and c.get("r1") is not None and (c.get("r1") or 0) > 0 else (0 if c.get("sup") and c.get("r1") == 0 else None),
                     "result": c.get("result"), "score": c.get("score")})
        if t.get("price") and t.get("area") and t.get("tot"):
            w_amt += t["price"] * t["tot"]; w_area += t["area"] / PY * t["tot"]
    sup = sum((r["sup"] or 0) for r in rows) or None
    r1 = sum((r["r1"] or 0) for r in rows) if cmp_ else None
    r2 = sum((r["r2"] or 0) for r in rows) if cmp_ else None
    results = [r["result"] or "" for r in rows]
    if not cmp_ or not rows or not cm:
        status = None
    elif any("미도래" in x for x in results):
        status = "접수 전"
    elif any("접수중" in x or "접수 중" in x for x in results):
        status = "접수 중"
    else:
        short = sum(max(0, (r["sup"] or 0) - (r["r1"] or 0) - (r["r2"] or 0)) for r in rows)
        if short > 0:
            status = f"미달 {short:,}세대"
        elif all("마감" in x for x in results) and all("1순위" in x for x in results):
            status = "1순위 마감"
        elif all("마감" in x for x in results):
            status = "순위 내 마감"
        else:
            status = "접수 종료"
    if status in (None, "접수 전"):
        for r in rows:
            r["rate"] = None
    t84 = [r for r in rows if r["ty"].startswith("084") and r.get("price")]
    sido, sgg = base["sido"], None
    if det.get("addr"):
        tok = det["addr"].split()
        if len(tok) >= 2:
            sgg = tok[1]
            if tok[0].startswith("세종"):
                sgg = "세종시"
    return {
        "pb": base["pb"], "hm": base["hm"], "name": base["name"], "sido": sido, "sgg": sgg, "addr": det.get("addr"),
        "kind": base["kind"], "sale": base["sale"], "builder": base["builder"] or det.get("constructor"),
        "developer": det.get("developer"), "notice": base["notice"], "period": base["period"], "winner": base["winner"],
        "contract": det.get("contract"), "movein": det.get("movein"), "units": det.get("units"),
        "sup": sup, "r1": r1, "r2": r2,
        "rate": None if status in (None, "접수 전") else (round(r1 / sup, 2) if sup and r1 else (0 if sup and r1 == 0 else None)),
        "status": status, "p84": max((r["price"] for r in t84), default=None),
        "ppy84": round(sum(r["ppy"] for r in t84) / len(t84)) if t84 else None,
        "ppy": round(w_amt / w_area) if w_area else None, "types": rows,
    }


def collect_applyhome(s, status) -> None:
    cache_f = CACHE / "applyhome.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    try:
        lst = ah_list(s)
    except Exception as e:  # noqa: BLE001
        status["sources"]["청약홈"] = {"ok": False, "error": str(e)[:300]}
        log("청약홈 목록 실패", e)
        return
    today = now_kst().date()
    out, fails, fetched = [], 0, 0
    for i, b in enumerate(lst):
        c = cache.get(b["pb"], {})
        try:
            if not c.get("det"):
                c["det"] = ah_detail(s, b["hm"], b["pb"]); fetched += 1; time.sleep(0.35)
            win = None
            try:
                win = dt.date.fromisoformat(b["winner"][:10])
            except Exception:  # noqa: BLE001
                pass
            final = c.get("cmp_final")
            st0 = (c.get("cmp") or {}).get("types", [])
            if not final:
                start = None
                try:
                    start = dt.date.fromisoformat(b["period"][:10])
                except Exception:  # noqa: BLE001
                    pass
                if start is None or start <= today or not st0:
                    c["cmp"] = ah_competition(s, b["hm"], b["pb"]); fetched += 1; time.sleep(0.35)
                    res = " ".join(x.get("result") or "" for x in c["cmp"]["types"])
                    if win and today >= win + dt.timedelta(days=2) and "접수중" not in res and "미도래" not in res:
                        c["cmp_final"] = True
            c["base"] = b
            cache[b["pb"]] = c
        except Exception as e:  # noqa: BLE001
            fails += 1
            log("청약홈 단지 실패", b["name"], e)
        if c.get("det"):
            out.append(summarize(b, c["det"], c.get("cmp")))
        if (i + 1) % 50 == 0:
            log(f"  {i + 1}/{len(lst)} (새로 받은 페이지 {fetched})")
    keep = {b["pb"] for b in lst}
    cache = {k: v for k, v in cache.items() if k in keep}
    CACHE.mkdir(exist_ok=True)
    cache_f.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))
    out.sort(key=lambda x: (x["notice"], x["pb"]), reverse=True)
    (DATA / "subscriptions.json").write_text(json.dumps({"generated": now_kst().strftime("%Y-%m-%d %H:%M"),
        "months": MONTHS_BACK, "rows": out}, ensure_ascii=False, separators=(",", ":")))
    status["sources"]["청약홈"] = {"ok": fails <= max(3, len(lst) // 20), "rows": len(out), "fails": fails, "fetched": fetched}
    log("청약홈 완료", len(out), "단지, 실패", fails)


# ───────────────────────── 미분양 ─────────────────────────
def molit(s, fid, style, start, end):
    s.get(f"{MOLIT}/portal/cate/statView.do?hRsId=32&hFormId={fid}", timeout=60)
    r = req(s, "GET", f"{MOLIT}/portal/stat/data.do?formId={fid}&styleNum={style}&apprYn=Y&startDate={start}&endDate={end}", timeout=180)
    j = r.json()
    if not j.get("result"):
        raise RuntimeError(f"통계누리 응답 오류 {fid}: {str(j)[:200]}")
    return j["data"]


def collect_unsold(s, status) -> None:
    end = now_kst().date().replace(day=1)
    y, m = end.year, end.month - UNSOLD_MONTHS - 1
    while m <= 0:
        y, m = y - 1, m + 12
    start = f"{y}{m:02d}"
    try:
        tot = molit(s, "2082", "128", start, end.strftime("%Y%m"))
        done = molit(s, "5328", "1", start, end.strftime("%Y%m"))
    except Exception as e:  # noqa: BLE001
        status["sources"]["통계누리"] = {"ok": False, "error": str(e)[:300]}
        log("통계누리 실패", e)
        return
    months = sorted({x["0"] for x in tot})[-(UNSOLD_MONTHS):]
    reg: dict[tuple, dict] = {}
    order = []

    def slot(sido, sgg):
        k = (sido, sgg)
        if k not in reg:
            reg[k] = {"sido": sido, "sgg": sgg, "total": [None] * len(months), "done": [None] * len(months)}
            order.append(k)
        return reg[k]

    mi = {mm: i for i, mm in enumerate(months)}
    # 2026년 광주·전남 통합 이후 통계누리는 '전남광주'로 공표한다. 과거 월도 같은 이름으로 합쳐 추이가 끊기지 않게 한다.
    merged = {x["1"] for x in tot if x["0"] == months[-1]}
    alias = {"광주": "전남광주", "전남": "전남광주"} if "전남광주" in merged else {}

    def put(arr, i, v):
        if v is not None:
            arr[i] = (arr[i] or 0) + v

    for x in tot:
        if x["0"] not in mi:
            continue
        sgg = "계" if x["2"] in ("계", "합계") else x["2"]
        put(slot(alias.get(x["1"], x["1"]), sgg)["total"], mi[x["0"]], num(x["3"]))
    for x in done:
        if x["0"] not in mi or x["3"] != "계" or x["4"] != "계":
            continue
        if x["1"] == "전국":
            continue
        sgg = "계" if x["2"] in ("계", "합계") else x["2"]
        put(slot(alias.get(x["1"], x["1"]), sgg)["done"], mi[x["0"]], num(x["5"]))
    nat_done = {x["0"]: num(x["5"]) for x in done if x["1"] == "전국" and x["3"] == "계" and x["4"] == "계"}
    sidos = [r for r in reg.values() if r["sgg"] == "계"]
    cap = {"서울", "인천", "경기"}

    def agg(rows, key):
        return [sum((r[key][i] or 0) for r in rows) if any(r[key][i] is not None for r in rows) else None for i in range(len(months))]

    summary = {
        "nation": {"total": agg(sidos, "total"), "done": [nat_done.get(mm) for mm in months]},
        "capital": {"total": agg([r for r in sidos if r["sido"] in cap], "total"), "done": agg([r for r in sidos if r["sido"] in cap], "done")},
        "local": {"total": agg([r for r in sidos if r["sido"] not in cap], "total"), "done": agg([r for r in sidos if r["sido"] not in cap], "done")},
    }
    (DATA / "unsold.json").write_text(json.dumps({"generated": now_kst().strftime("%Y-%m-%d %H:%M"), "months": months,
        "summary": summary, "regions": [reg[k] for k in order]}, ensure_ascii=False, separators=(",", ":")))
    status["sources"]["통계누리"] = {"ok": True, "latest": months[-1] if months else None, "regions": len(order)}
    log("통계누리 완료", months[-1] if months else None, len(order), "지역")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    st_f = DATA / "status.json"
    status = {"generated": now_kst().strftime("%Y-%m-%d %H:%M"), "sources": {}}
    s = sess()
    only = sys.argv[1:] or ["unsold", "applyhome"]
    if "unsold" in only:
        collect_unsold(s, status)
    if "applyhome" in only:
        collect_applyhome(s, status)
    old = json.loads(st_f.read_text()) if st_f.exists() else {"sources": {}}
    for k, v in old.get("sources", {}).items():
        status["sources"].setdefault(k, v)
    st_f.write_text(json.dumps(status, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
