"""실거래가 지도 · 아파트 시가총액(추정) vs M2(통화량) 자료.

1) 국토교통부 아파트 매매 실거래가(상세) → 달별 요약 cache/rt/YYYYMM.json.gz
   - DATA_GO_KR_KEY 가 있을 때만 받는다: 최근 4개월(신고 30일 · 해제 반영)과 빠진 달.
   - 키 · 요청 주소는 어떤 출력(로그 포함)에도 남기지 않는다(clean()).
   - 한 달 파일: {"ym", "apt": {단지: [시도|시군구|동, 전용 평당가 중위(만원), 건수]},
                  "dong": {시도|시군구|동: [건수, 거래금액 합(만원), 전용면적 합, 공급면적(환산) 합]}}
2) 지역별 달 지수 · 평당가 → site/data/rt/main.json(전국 · 시도 · 시군구), d00~.json(법정동, 시도별), sum.json
   - 지수: 같은 단지 반복거래 고정효과 회귀(단지-달 하나 = 가중 1, 달 사이 매끄럽게) — 한국부동산원
     아파트 매매 실거래가격지수(반복매매)와 서울 · 부산 15년 누적이 1~2% 안으로 맞는 방식.
     2006년 1월(실거래가 공개 시작)부터 — 지도 기간 버튼은 최대 20년.
   - 평당가: 끝 달까지 3개월 [거래금액 합 ÷ 면적 합]. 공급면적은 청약홈 분양 주택형의 공급 ÷ 전용 비율로 환산.
   - 지역별 세대수 가중: 법정동 평당가 × K-apt 동별 세대수(공공임대 제외, cache/kapt_market.json).
3) 아파트 시가총액(추정) vs M2 → site/data/hm.json
   - cap(t) = Σ시도 세대수(t) × 기준가격 × 지수(t)/지수(기준월). 지수는 한국부동산원(ECOS 901Y089),
     발표 전 달은 2)의 우리 지수를 이어 붙인 잠정치. M2는 ECOS 161Y008(말잔 · 원계열).
"""
from __future__ import annotations

import concurrent.futures as cf
import gzip
import json
import math
import os
import re
import statistics as st
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from collect import CACHE, DATA, log, now_kst

HERE = Path(__file__).resolve().parent
RTC = CACHE / "rt"                     # 달별 요약
OUTR = DATA / "rt"
FIRST = "200601"                       # 국토부 실거래가 공개 시작(2006년 1월) — 20년 변동률까지
BUDGET = 11 * 60                       # 한 번 돌 때 지난 달 채우기에 쓰는 시간(초) — 넘으면 다음 번에 이어서
EP = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
ECOS_KEY = os.environ.get("ECOS_API_KEY", "").strip()
SUP_R = [(40, 1.413), (59, 1.385), (74, 1.352), (84, 1.324), (105, 1.306), (135, 1.327)]   # 청약홈 주택형 공급 ÷ 전용 중위
SHORT = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "대전광역시": "대전", "울산광역시": "울산",
         "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북",
         "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주"}
SIDO_OF = lambda s: "전남광주" if s in ("광주", "전남") else s  # noqa: E731


def clean(x) -> str:
    s = str(x)
    for k in (KEY, urllib.parse.quote(KEY, safe=""), urllib.parse.quote_plus(KEY), ECOS_KEY):
        if k:
            s = s.replace(k, "***")
    s = re.sub(r"serviceKey=[^&\s'\")]+", "serviceKey=***", s)
    return re.sub(r"https?://\S+", "<주소>", s)


def sup_ratio(ar: float) -> float:
    if ar <= SUP_R[0][0]:
        return SUP_R[0][1]
    for (a0, r0), (a1, r1) in zip(SUP_R, SUP_R[1:]):
        if ar <= a1:
            return r0 + (r1 - r0) * (ar - a0) / (a1 - a0)
    return SUP_R[-1][1]


def nxt(ym: str, k: int = 1) -> str:
    t = int(ym[:4]) * 12 + int(ym[4:]) - 1 + k
    return f"{t // 12}{t % 12 + 1:02d}"


def last_full_month() -> str:
    n = now_kst()
    return nxt(f"{n.year}{n.month:02d}", -1)


def write_json(path: Path, obj) -> None:
    """반쯤 쓴 파일이 배포되지 않게 임시 파일에 쓴 뒤 바꿔 넣는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, path)


# ── 1) 실거래 달별 요약 ─────────────────────────────────────────────

def codes() -> dict:
    out = {}
    for sd, c5, umd, sgg, dong, ri in json.loads((HERE / "bjd_units.json").read_text(encoding="utf-8")):
        s = ("광주" if re.search(r"구$", sgg) else "전남") if sd.startswith("전남광주") else SHORT.get(sd, sd)
        out[c5] = (s, "세종시" if s == "세종" else re.sub(r"^(.+?시)\s.+구$", r"\1", sgg))
    return out


def rt_fetch(status: dict) -> None:
    RTC.mkdir(parents=True, exist_ok=True)
    have = {f.name[:6] for f in RTC.glob("*.json.gz")}
    end = last_full_month()
    allm, m = [], FIRST
    while m <= end:
        allm.append(m); m = nxt(m)
    want = sorted({*allm[-4:], *[x for x in allm if x not in have]}, reverse=True)   # 최근 달부터(최근 4개월은 매번 다시)
    if not KEY:
        log("실거래가: 공공데이터포털 키(DATA_GO_KR_KEY)가 없어 저장된 자료로만 만듦")
        return
    CODE = codes(); sess = requests.Session(); lock = threading.Lock(); stop = [False]; calls = [0]; fails = [0]
    # 먼저 한 번만 불러 접속 · 인증키를 확인한다(안 되면 오래 기다리지 않고 저장된 자료로)
    pre = None
    for att in range(3):
        try:
            r = sess.get(EP, params={"serviceKey": KEY, "LAWD_CD": "11680", "DEAL_YMD": end, "pageNo": 1, "numOfRows": 1}, timeout=(10, 30))
            pre = (r.status_code, (ET.fromstring(r.content).findtext(".//errMsg") or ET.fromstring(r.content).findtext(".//returnAuthMsg") or "").strip()); break
        except Exception as e:  # noqa: BLE001
            log("실거래가: 접속 확인 실패", att + 1, type(e).__name__); time.sleep(5 + att * 5)
    if pre is None:
        status["sources"]["국토부 실거래가"] = {"ok": False, "error": "공공데이터포털 접속이 안 됨 · 저장된 자료 표시"}; return
    if pre[1]:
        log("실거래가: 응답 오류", pre[0], clean(pre[1])[:60])
        status["sources"]["국토부 실거래가"] = {"ok": False, "error": "공공데이터포털 인증키 · 응답 오류 · 저장된 자료 표시"}; return

    def fetch(code, ymd):
        rows, page = [], 1
        while True:
            if stop[0]:
                return None
            for att in range(4):
                try:
                    r = sess.get(EP, params={"serviceKey": KEY, "LAWD_CD": code, "DEAL_YMD": ymd, "pageNo": page, "numOfRows": 1000}, timeout=(15, 60))
                    with lock:
                        calls[0] += 1
                    root = ET.fromstring(r.content)
                    err = (root.findtext(".//errMsg") or root.findtext(".//returnAuthMsg") or "").strip()
                    if "LIMITED" in err or "EXCEEDS" in err or (root.findtext(".//resultCode") or "").strip() == "22":
                        stop[0] = True; log("실거래가: 호출 한도 초과 → 이번엔 여기까지"); return None
                    if err:
                        raise RuntimeError(err)
                    rows += [{c.tag: (c.text or "").strip() for c in it} for it in root.findall(".//item")]
                    tot = int((root.findtext(".//totalCount") or "0").strip() or 0)
                    break
                except Exception as e:  # noqa: BLE001
                    if att == 3:
                        with lock:
                            fails[0] += 1
                            if fails[0] >= 30:
                                stop[0] = True
                        log("실거래가: 실패", code, ymd, type(e).__name__); return None   # 오류 문구에는 요청 주소가 들어 있어 남기지 않는다
                    time.sleep(2 + att * 3)
            if page * 1000 >= tot:
                return rows
            page += 1

    done, t0, recent = [], time.time(), set(allm[-4:])
    for ym in want:
        if ym not in recent and time.time() - t0 > BUDGET:
            log("실거래가: 시간 한도 — 남은 지난 달은 다음 번에", len([x for x in want if x not in done and x not in recent])); break
        with cf.ThreadPoolExecutor(5) as ex:
            res = list(ex.map(lambda c: (c, fetch(c, ym)), list(CODE)))
        if stop[0]:
            break
        if any(v is None for _, v in res):
            log("실거래가: 빠진 지역이 있어 저장 안 함", ym); continue
        seen, apt, dong = set(), {}, {}
        for c, rows in res:
            for x in rows:
                if (x.get("cdealType") or "").strip() in ("O", "o"):
                    continue                                   # 해제된 거래
                try:
                    amt = float((x.get("dealAmount") or "0").replace(",", "")); ar = float(x.get("excluUseAr") or 0)
                except ValueError:
                    continue
                if amt <= 0 or ar < 10:
                    continue
                d = (x.get("umdNm") or "").strip()
                sig = (x.get("aptSeq") or x.get("aptNm"), d, x.get("dealYear"), x.get("dealMonth"), x.get("dealDay"), x.get("dealAmount"), x.get("floor"), x.get("excluUseAr"))
                if sig in seen:
                    continue
                seen.add(sig)
                s, g = CODE[c]; key = f"{s}|{g}|{d}"
                a = x.get("aptSeq") or f"{c}|{d}|{x.get('jibun', '')}|{x.get('aptNm', '')}"
                apt.setdefault(a, [key, []])[1].append(amt / ar * 3.3058)
                q = dong.setdefault(key, [0, 0.0, 0.0, 0.0]); q[0] += 1; q[1] += amt; q[2] += ar; q[3] += ar * sup_ratio(ar)
        out = {"ym": ym, "apt": {a: [k, round(st.median(v), 1), len(v)] for a, (k, v) in apt.items()},
               "dong": {k: [q[0], round(q[1]), round(q[2], 1), round(q[3], 1)] for k, q in dong.items()}}
        tmp = RTC / f"{ym}.json.gz.tmp"; tmp.write_bytes(gzip.compress(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
        os.replace(tmp, RTC / f"{ym}.json.gz"); done.append(ym)
    log("실거래가: 받은 달", len(done), "/", len(want), "호출", calls[0], f"{time.time() - t0:.0f}초")
    ok = not stop[0] and recent <= set(done)                  # 최근 4개월을 다 받았으면 정상(지난 달 채우기는 이어서)
    left = len(set(allm) - {f.name[:6] for f in RTC.glob("*.json.gz")})
    status["sources"]["국토부 실거래가"] = {"ok": ok, "latest": max(done) if done else None, "months": len(done),
                                       **({"backfill_left": left} if left else {}),
                                       **({} if ok else {"error": "일부 달을 받지 못함 · 받은 달까지 반영"})}


# ── 2) 지역별 달 지수 · 평당가 ──────────────────────────────────────

def kapt_units() -> dict:
    f = CACHE / "kapt_market.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def rt_build(status: dict) -> dict | None:
    import numpy as np
    files = sorted(RTC.glob("*.json.gz"))
    if not files:
        log("실거래가: 저장된 달별 자료가 없음"); return None
    ms = [f.name[:6] for f in files]
    # 끝 달부터 거꾸로 빠짐없이 이어진 구간만 쓴다(지난 달을 채우는 중이거나 한 달이 빠져도 지도는 계속 갱신)
    have, m = set(ms), ms[-1]
    while nxt(m, -1) in have:
        m = nxt(m, -1)
    if m != ms[0]:
        log("실거래가: 이어지지 않는 앞쪽 달은 이번 지수에서 뺌", ms[0], "~", nxt(m, -1))
    files = [f for f in files if f.name[:6] >= m]
    allm, m = [], m
    while m <= ms[-1]:
        allm.append(m); m = nxt(m)
    T = len(allm); TI = {m: i for i, m in enumerate(allm)}

    METRO = ("부산", "대구", "광주", "대전", "울산")          # 5대 광역시(접힌 칸 요약용 묶음)

    def lvkeys(k):
        s, g, d = k.split("|")
        base = (("all", "전국"), ("sido", SIDO_OF(s)), ("sgg", f"{s}|{g}"), ("dong", k))
        return base + ((("grp", "5대광역시"),) if s in METRO else ())
    obs, adong, sums = {}, {}, {}
    for f in files:
        d = json.loads(gzip.decompress(f.read_bytes())); t = TI[d["ym"]]
        for a, (k, p, n) in d["apt"].items():
            if p > 0:
                obs.setdefault(a, []).append((t, math.log(p))); adong[a] = k
        for k, q in d["dong"].items():
            for lv, kk in lvkeys(k):
                arr = sums.get((lv, kk))
                if arr is None:
                    arr = sums[(lv, kk)] = np.zeros((T, 4))
                arr[t] += q
    # 지수: 단지 고정효과 회귀 — ln 평당가(단지, 달) = x_달 + 단지 효과, 단지 안에서 평균을 뺀 정규방정식을 지역별로 더함
    ALPHA = 2.0
    Dm = np.zeros((T - 1, T)); Dm[np.arange(T - 1), np.arange(T - 1)] = -1; Dm[np.arange(T - 1), np.arange(1, T)] = 1
    Pn = Dm.T @ Dm
    groups = {}
    for a, ob in obs.items():
        if len(ob) >= 2:
            for lv, kk in lvkeys(adong[a]):
                groups.setdefault((lv, kk), []).append(a)
    idx = {}
    for (lv, kk), apts in groups.items():
        if sum(len(obs[a]) for a in apts) < (20 if lv == "dong" else 30):
            continue
        A = np.zeros((T, T)); b = np.zeros(T)
        for a in apts:
            ob = obs[a]; t = np.fromiter((o[0] for o in ob), int); lp = np.fromiter((o[1] for o in ob), float)
            A[np.ix_(t, t)] -= 1.0 / len(ob); A[t, t] += 1.0; b[t] += lp - lp.mean()
        M = A + ALPHA * Pn; x = np.zeros(T); x[1:] = np.linalg.solve(M[1:, 1:], b[1:])
        idx[(lv, kk)] = x
    # 3개월 창 평당가
    def win3(arr):
        c = np.cumsum(np.vstack([np.zeros((1, 4)), arr]), axis=0); w = c[3:] - c[:-3]
        return np.vstack([np.full((2, 4), np.nan), w])
    SP = {k: win3(v) for k, v in sums.items()}

    def pp(w, col):
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(w[:, 0] > 0, w[:, 1] / w[:, col] * 3.3058, np.nan)
    # 지역별 세대수 가중(법정동 평당가 × K-apt 동별 세대수)
    sg_keys = {k for (lv, k) in sums if lv == "sgg"}

    def gname(s, sg):
        if s == "세종":
            return "세종시"
        g = re.sub(r"^(.+?시)\s.+구$", r"\1", sg)
        if f"{s}|{g}" in sg_keys:
            return g
        for k in (2, 3):                               # '천안서북구'처럼 시 이름이 붙어 쓰인 구
            if sg.endswith("구") and f"{s}|{sg[:k]}시" in sg_keys:
                return sg[:k] + "시"
        return g
    pw_acc = {}
    for key, n in kapt_units().get("dong", {}).items():
        s, sg, dong, em = key.split("|")
        if s == "전남광주":
            s = "광주" if re.search(r"구$", sg) else "전남"
        g = gname(s, sg); d = f"{em} {dong}".strip() if em else dong; dk, sk = f"{s}|{g}|{d}", f"{s}|{g}"
        pd = pp(SP[("dong", dk)], 3) if ("dong", dk) in SP else np.full(T, np.nan)
        nd = SP[("dong", dk)][:, 0] if ("dong", dk) in SP else np.zeros(T)
        ps = pp(SP[("sgg", sk)], 3) if ("sgg", sk) in SP else np.full(T, np.nan)
        pv = pp(SP[("sido", SIDO_OF(s))], 3) if ("sido", SIDO_OF(s)) in SP else np.full(T, np.nan)
        v = np.where((nd >= 3) & ~np.isnan(pd), pd, np.where(~np.isnan(ps), ps, pv))
        for kk in (("sgg", sk), ("sido", SIDO_OF(s)), ("all", "전국")):
            a = pw_acc.setdefault(kk, [np.zeros(T), np.zeros(T)]); ok = ~np.isnan(v); a[0][ok] += v[ok] * n; a[1][ok] += n

    def ints(a):
        return [None if (x is None or (isinstance(x, float) and math.isnan(x))) else int(round(x)) for x in a]
    months = [f"{m[:4]}-{m[4:]}" for m in allm]
    levels = {"all": {}, "sido": {}, "sgg": {}, "grp": {}}; dongs = {}
    for (lv, kk), w in SP.items():
        e = {"p": ints(pp(w, 3)), "pe": ints(pp(w, 2)), "n": ints(w[:, 0])}
        if (lv, kk) in idx:
            e["i"] = [int(round(10000 * math.exp(v))) for v in idx[(lv, kk)]]
        if (lv, kk) in pw_acc:
            a = pw_acc[(lv, kk)]
            with np.errstate(invalid="ignore", divide="ignore"):
                e["pw"] = ints(np.where(a[1] > 0, a[0] / a[1], np.nan))
        (dongs if lv == "dong" else levels[lv])[kk] = e
    # 법정동: 시도별 파일(지수는 1/10로 줄여 앞 달과의 차이만, 빈 달은 0)
    grp = {}
    for k, e in dongs.items():
        r = {}
        if "i" in e:
            v = [round(x / 10) for x in e["i"]]; r["i"] = [v[0]] + [b - a for a, b in zip(v, v[1:])]
        for c in ("p", "pe", "n"):
            r[c] = [x or 0 for x in e[c]]
        grp.setdefault(SIDO_OF(k.split("|")[0]), {})[k] = r
    dfiles = {}
    for i, (sd, d) in enumerate(sorted(grp.items())):
        write_json(OUTR / f"d{i:02d}.json", d); dfiles[sd] = f"data/rt/d{i:02d}.json"
    # 법정동 대표점(지도에 점 찍을 자리)
    BP = json.loads((HERE / "bjd_pts.json").read_text(encoding="utf-8")); dp = {}
    for k in dongs:
        s, g, d = k.split("|"); c = BP.get(s, {}).get(d)
        if not c:
            continue
        pick = [x for x in c if g in x[0] or g == "세종시"] or (c if len(c) == 1 else [])
        if pick:
            dp[k] = [round(pick[0][1], 5), round(pick[0][2], 5)]
    main = {"asof": months[-1], "months": months, "generated": now_kst().strftime("%Y-%m-%d %H:%M"), "levels": levels, "dfiles": dfiles, "dpts": dp}
    write_json(OUTR / "main.json", main)
    a = levels["all"]["전국"]; N = T - 1
    c12 = round((a["i"][N] / a["i"][N - 12] - 1) * 100, 2) if "i" in a and N >= 12 else None
    # 접힌 칸 요약: 최근 10년 연평균 상승률(지수 기준) — 서울 · 경기 · 5대 광역시
    def cagr(e, yrs=10):
        k = 12 * yrs
        return round(((e["i"][N] / e["i"][N - k]) ** (1 / yrs) - 1) * 100, 4) if e and "i" in e and N >= k else None   # 화면에서 소수 한 자리로
    ten = {"서울": cagr(levels["sido"].get("서울")), "경기": cagr(levels["sido"].get("경기")), "5대광역시": cagr(levels["grp"].get("5대광역시"))}
    def cum(e, yrs=10):          # 최근 10년 누적 상승률(접힌 칸 요약에 씀)
        k = 12 * yrs
        return round((e["i"][N] / e["i"][N - k] - 1) * 100, 4) if e and "i" in e and N >= k else None
    c10 = {"서울": cum(levels["sido"].get("서울")), "경기": cum(levels["sido"].get("경기")), "5대광역시": cum(levels["grp"].get("5대광역시"))}
    write_json(OUTR / "sum.json", {"asof": months[-1], "from": months[N - 120] if N >= 120 else None, "p": (a.get("pw") or a["p"])[N], "c12": c12, "c10": c10, "a10": ten})
    log("실거래가: 지역 지수", {lv: sum(1 for (l, _) in idx if l == lv) for lv in ("all", "sido", "sgg", "dong")}, "마지막 달", months[-1])
    return {"months": months, "levels": levels, "sums": {k: v for k, v in sums.items() if k[0] == "sido" or k[0] == "all"},
            "dong_sums": {k: v for k, v in sums.items() if k[0] == "dong"}}


# ── 3) 아파트 시가총액(추정) vs M2 ──────────────────────────────────

ECOS = "https://ecos.bok.or.kr/api/StatisticSearch"
IDX_ITEMS = {"전국": "100", "서울": "200", "부산": "500", "대구": "600", "인천": "700", "광주": "800", "대전": "900", "울산": "A00", "세종": "B00",
             "경기": "C00", "강원": "D00", "충북": "E00", "충남": "F00", "전북": "G00", "전남": "H00", "경북": "I00", "경남": "J00", "제주": "K00"}


def ecos_series(stat: str, item: str, start: str, end: str) -> dict:
    key = ECOS_KEY or "sample"; step = 1000 if ECOS_KEY else 10      # 공개 견본 키는 한 번에 10줄까지
    out, y = {}, start
    s = requests.Session()
    while y <= end:
        z = min(end, nxt(y, step - 1))
        url = f"{ECOS}/{key}/json/kr/1/{step}/{stat}/M/{y}/{z}/{item}"
        for att in range(5):
            try:
                rows = s.get(url, timeout=(15, 40)).json().get("StatisticSearch", {}).get("row", []); break
            except Exception as e:  # noqa: BLE001
                if att == 4:
                    raise RuntimeError(f"ECOS {stat} {item} 실패: {type(e).__name__}")
                time.sleep(5 * (att + 1))
        for r in rows:
            out[f"{r['TIME'][:4]}-{r['TIME'][4:6]}"] = float(r["DATA_VALUE"])
        y = nxt(z)
    return out


def ecos_update() -> dict:
    """한국부동산원 아파트 매매 실거래가격지수(901Y089) · M2(161Y008 BBGA00). 최근 2년만 다시 받고,
    겹치는 달 값이 달라졌으면(기준월 변경 등) 처음부터 다시 받는다."""
    f = CACHE / "ecos.json"
    old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"idx": {}, "m2": {}}
    end = last_full_month(); start = nxt(end, -23)

    def merge(cur: dict, stat, item, first):
        new = ecos_series(stat, item, start, end)
        same = [m for m in new if m in cur and m <= f"{nxt(end, -6)[:4]}-{nxt(end, -6)[4:]}"]   # 최근 반년은 원래 고쳐지므로 그 앞 달로만 확인
        if not cur or not same or any(abs(new[m] / cur[m] - 1) > 1e-3 for m in same):
            return ecos_series(stat, item, first, end)               # 처음부터
        return {**cur, **new}
    idx = {k: merge(old["idx"].get(k, {}), "901Y089", it, "200601") for k, it in IDX_ITEMS.items()}
    m2 = merge(old.get("m2", {}), "161Y008", "BBGA00", "200301")
    res = {"idx": idx, "m2": m2}
    write_json(f, res)
    return res


def hm_build(status: dict, rt: dict | None) -> None:
    try:
        E = ecos_update()
        status["sources"]["한국은행 ECOS"] = {"ok": True, "latest_idx": max(E["idx"]["전국"]), "latest_m2": max(E["m2"])}
    except Exception as e:  # noqa: BLE001 — 접속이 안 되면 저장된 값으로 만들고 상태에만 표시
        log("ECOS 받기 실패 → 저장된 값 사용", clean(e)[:120])
        E = json.loads((CACHE / "ecos.json").read_text(encoding="utf-8"))
        status["sources"]["한국은행 ECOS"] = {"ok": False, "error": "접속 실패 · 저장된 값으로 계산"}
    IDX, M2 = E["idx"], E["m2"]
    K = kapt_units()
    if not K.get("tot"):
        log("시가총액: K-apt 세대수 자료가 없음(입주 · 수요 단계가 먼저 돌아야 함)"); return
    official_last = max(IDX["전국"]); prov = None
    # 부동산원 지수 발표 전 달: 우리 지수(같은 반복매매 방식)를 이어 붙인다(잠정)
    if rt:
        HM = rt["months"]; ext = [m for m in HM if official_last < m <= max(M2)]
        if ext and official_last in HM:
            b0 = HM.index(official_last)
            for r, ser in IDX.items():
                e = rt["levels"]["all"]["전국"] if r == "전국" else rt["levels"]["sido"].get(SIDO_OF(r))
                if e and "i" in e:
                    for m in ext:
                        ser[m] = round(ser[official_last] * e["i"][HM.index(m)] / e["i"][b0], 3)
            prov = ext[0]
    end = min(max(IDX["전국"]), max(M2)); months = sorted(m for m in IDX["전국"] if m <= end)
    REG = list(K["tot"])

    def stock(r, ym):
        k = ym.replace("-", "")
        return sum(v for d, v in K["tot"].get(r, {}).items() if d <= k) - sum(v for d, v in K.get("pub", {}).get(r, {}).items() if d <= k)
    ST = {r: [stock(r, m) for m in months] for r in REG}
    units = [sum(ST[r][i] for r in REG) for i in range(len(months))]
    # 기준 가격: 부동산원 지수가 있는 최근 3개월의 시도별 세대당 평균 거래금액(기준월로 옮겨 더함)
    cap, anc = [None] * len(months), None
    if rt:
        HMs = rt["months"]; sido_n = {}
        for (lv, k), arr in rt["dong_sums"].items():
            s = k.split("|")[0]
            for t, m in enumerate(HMs):
                if arr[t][0] > 0:
                    q = sido_n.setdefault((s, m), [0.0, 0.0]); q[0] += arr[t][0]; q[1] += arr[t][1]
        am = [m for m in HMs if m <= official_last and m in IDX["전국"]][-3:]
        if am:
            base = am[-1]; price, cnt = {}, {}
            for r in REG:
                n = s_ = 0.0
                for m in am:
                    q = sido_n.get((r, m))
                    if q and IDX.get(r, {}).get(m):
                        n += q[0]; s_ += q[1] * IDX[r][base] / IDX[r][m]
                if n >= 30:
                    price[r] = round(s_ / n); cnt[r] = int(n)
            if all(r in price and r in IDX for r in REG):
                cap = [round(sum(ST[r][i] * price[r] * IDX[r][m] / IDX[r][base] for r in REG) / 1e8, 1) for i, m in enumerate(months)]
                anc = {"months": am, "base": base, "n": sum(cnt.values())}
    cpx = [units[i] * IDX["전국"][m] for i, m in enumerate(months)]
    foot = ("자료: 한국은행 ECOS(M2 광의통화 말잔 · 원계열, 한국부동산원 아파트 매매 실거래가격지수), 공동주택관리정보시스템(K-apt) "
            f"단지 기본정보({K.get('basis', '')}), 국토교통부 아파트 매매 실거래가. 아파트 시가총액은 이 자료로 계산한 추정치입니다.")
    if anc:
        foot += " 세대당 기준 가격은 %s년 %d~%d월 실거래 %s건의 시 · 도별 평균입니다." % (anc["months"][0][:4], int(anc["months"][0][5:]), int(anc["months"][-1][5:]), f"{anc['n']:,}")
    if prov:
        foot += " %s년 %d월부터는 한국부동산원 지수 발표 전이라 같은 방식(반복매매)으로 국토부 실거래에서 만든 지수를 이어 붙인 잠정치입니다." % (prov[:4], int(prov[5:]))
    out = {"months": months, "m2": [round(M2[m] / 1000, 1) for m in months], "units": units, "cap": cap,
           "cpx": [round(v / cpx[0] * 100, 2) for v in cpx], "prov": prov, "anchor": anc, "foot": foot,
           "generated": now_kst().strftime("%Y-%m-%d %H:%M")}
    write_json(DATA / "hm.json", out)
    log("시가총액:", months[-1], "시총", cap[-1], "조 · M2", out["m2"][-1], "조", "(잠정 " + prov + "~)" if prov else "")


def collect_market(status: dict) -> None:
    try:
        rt_fetch(status)
    except Exception as e:  # noqa: BLE001
        log("실거래가 수집 오류", type(e).__name__, clean(e)[:120])
        status["sources"]["국토부 실거래가"] = {"ok": False, "error": "수집 오류 · 직전 값 표시"}
    rt = None
    try:
        rt = rt_build(status)
    except Exception as e:  # noqa: BLE001
        log("실거래가 지도 자료 만들기 오류", type(e).__name__, clean(e)[:160])
    try:
        hm_build(status, rt)
    except Exception as e:  # noqa: BLE001
        log("시가총액 자료 만들기 오류", type(e).__name__, clean(e)[:160])
        status["sources"]["한국은행 ECOS"] = {"ok": False, "error": "수집 오류 · 직전 값 표시"}
