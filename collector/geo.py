"""청약 단지 위치(경도 · 위도)를 정한다. 지도에서 확대했을 때 단지별 점으로 쓴다.

순서
  ① 브이월드(국토교통부 공간정보 오픈플랫폼) 주소 → 좌표: GitHub Secrets에 VWORLD_KEY가 있고 접속될 때만
  ② 법정동 · 리 대표점(collector/bjd_pts.json, tools/make_bjd.py로 만든 고정 표): 주소의 읍 · 면 · 동 · 리 이름으로 찾는다
  ③ 둘 다 안 되면 위치 없음 → 화면은 시 · 군 · 구 점으로 표시
결과는 cache/geo.json에 단지(공고번호)별로 쌓아 두고, ②로 정한 단지는 ①이 될 때 조금씩 다시 시도한다.
"""
import json
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_BJD = None
VW = "https://api.vworld.kr/req/address"
TOK = re.compile(r"^[가-힣0-9·]+(?:동|가|리|읍|면)$")
BAD = ("신도시", "지구", "블록", "일원", "단지", "생활권", "택지")


def _bjd():
    global _BJD
    if _BJD is None:
        _BJD = json.loads((HERE / "bjd_pts.json").read_text())
    return _BJD


def _parts(addr):
    a = addr or ""
    inner = re.findall(r"\(([^()]*)\)", a)
    outer = re.sub(r"\([^()]*\)", " ", a)
    return [*inner, outer]


def bjd_point(sido, sgg, addr):
    T = _bjd().get(sido) or {}
    for s in _parts(addr):
        raw = [t.strip(",.;") for t in s.replace(",", " ").split()]
        toks = []
        for t in raw:
            if re.fullmatch(r"\d+가", t) and toks and toks[-1].endswith("동"):
                toks[-1] += t; continue                     # 수성동 4가 → 수성동4가
            m = re.fullmatch(r"([가-힣0-9·]+?(?:동|가|리|읍|면))(?:산?\d[\d\-]*)?", t)
            toks.append(m.group(1) if m else t)             # 신동720 → 신동
        for i, t in enumerate(toks):
            if not TOK.match(t) or any(b in t for b in BAD):
                continue
            keys = []
            if t.endswith(("읍", "면")) and i + 1 < len(toks) and toks[i + 1].strip(",.").endswith("리"):
                keys.append(f"{t} {toks[i + 1].strip(',.')}")
            keys.append(t)
            m2 = re.fullmatch(r"([가-힣]+?)\d+동", t)      # 행정동(간석1동 · 광명4동) → 법정동(간석동 · 광명동)
            if m2:
                keys.append(m2.group(1) + "동")
            if t.endswith("리"):
                keys += [k for k in T if k.endswith(" " + t)]
            for k in keys:
                c = T.get(k)
                if not c:
                    continue
                m = [x for x in c if sgg and sgg in x[0]] or (c if len(c) == 1 or sido == "세종" else [])
                if m:
                    return [m[0][1], m[0][2]]
    return None


def _vw_query(sido, sgg, addr):
    """브이월드에 물을 주소 후보(괄호 안 지번 주소 우선)."""
    out = []
    for s in _parts(addr):
        q = re.sub(r"\s*(일원|일대|번지|외\s*\d+\s*필지).*$", "", s.strip())
        q = re.sub(r"\b[A-Z]{0,3}\d*-?\d*\s*(BL|블록)\b.*$", "", q).strip(" ,")
        if not re.search(r"\d", q) or not re.search(r"[가-힣]{2,}", q):
            continue
        if sgg and sgg not in q and sido not in q:
            q = f"{sido} {sgg} {q}"
        out.append(q)
    return out


def vworld_point(s, key, sido, sgg, addr):
    for q in _vw_query(sido, sgg, addr):
        for typ in ("parcel", "road"):
            r = s.get(VW, params={"service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326",
                                  "type": typ, "refine": "true", "simple": "true", "format": "json", "address": q, "key": key},
                      timeout=10)
            j = r.json().get("response", {})
            if j.get("status") == "OK":
                p = j["result"]["point"]
                return [round(float(p["x"]), 5), round(float(p["y"]), 5)]
            if j.get("status") == "ERROR":
                raise RuntimeError(str(j.get("error"))[:200])
            time.sleep(0.1)
    return None


def locate(rows, s, cache_dir, log=print):
    key = os.environ.get("VWORLD_KEY", "").strip()
    f = cache_dir / "geo.json"
    cache = json.loads(f.read_text()) if f.exists() else {}
    vw_ok, budget, n = bool(key), 120, {"vworld": 0, "bjd": 0, "none": 0}
    for r in rows:
        c = cache.get(r["pb"])
        want_vw = vw_ok and budget > 0 and (not c or (c.get("src") != "vworld" and time.time() - c.get("vt", 0) > 7 * 86400))
        if c and not want_vw and c.get("src") != "none":
            r["ll"] = c["ll"]; n[c["src"]] += 1
            continue
        ll, src, vt = None, None, (c or {}).get("vt", 0)
        if want_vw:
            budget -= 1; vt = int(time.time())
            try:
                ll = vworld_point(s, key, r.get("sido") or "", r.get("sgg") or "", r.get("addr"))
                src = "vworld" if ll else None
            except Exception as e:  # noqa: BLE001  접속 안 되면(해외 차단 등) 이번 수집에서는 더 묻지 않는다
                vw_ok = False
                log("브이월드 주소 변환 중단", str(e)[:160])
        if not ll and c and c.get("src") in ("vworld", "bjd"):
            ll, src = c["ll"], c["src"]
        if not ll:
            ll = bjd_point(r.get("sido") or "", r.get("sgg") or "", r.get("addr"))
            src = "bjd" if ll else "none"
        cache[r["pb"]] = {"ll": ll, "src": src, **({"vt": vt} if vt else {})}
        if ll:
            r["ll"] = ll
        n[src] += 1
    keep = {r["pb"] for r in rows}
    f.write_text(json.dumps({k: v for k, v in cache.items() if k in keep}, ensure_ascii=False, separators=(",", ":")))
    log("단지 위치", n, "(브이월드 키 없음)" if not key else "")
    return n
