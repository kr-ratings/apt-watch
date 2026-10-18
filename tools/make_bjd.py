"""청약 단지 위치용 법정동 · 리 대표점 표(collector/bjd_pts.json)를 만든다. 행정구역 개편 때만 다시 실행.

입력(공개 자료, dc-watch에서 이미 만든 것):
  dc-watch/data/geo_bjd_pts.json     법정동(8자리) · 리(10자리) 코드별 대표점 [경도, 위도, 이름]  (도로명주소 DB 행정구역도)
  dc-watch/data/bjd_units_seed.json  행정안전부 법정동코드 [시도, 시군구코드, 법정동코드, 시군구, 읍면동, 리]  (개편 전 코드 — 대표점과 코드가 맞음)
  dc-watch/data/bjd_units.json       같은 자료의 현행본(인천 '26.7 개편 구 이름을 별칭으로 붙임)
출력: collector/bjd_pts.json  {"시도약칭": {"읍면동" 또는 "읍면 리": [[시군구 이름들, 경도, 위도], ...]}}
사용: python3 tools/make_bjd.py <dc-watch/data>
"""
import json, re, sys

SHORT = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "대전광역시": "대전", "울산광역시": "울산",
         "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원", "충청북도": "충북", "충청남도": "충남",
         "전북특별자치도": "전북", "전라북도": "전북", "광주광역시": "광주", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주"}


def short(sd, sg):
    if sd.endswith("통합특별시"):
        return "광주" if sg.endswith("구") else "전남"
    return SHORT.get(sd, sd)


def lv(sg):  # '고양시 일산동구' → '고양시'
    return re.sub(r"^(\S+시)\s?\S+구$", r"\1", sg if isinstance(sg, str) else "")


def S(v):
    return v if isinstance(v, str) else ""


def main(d):
    pts = json.load(open(f"{d}/geo_bjd_pts.json"))
    seed = json.load(open(f"{d}/bjd_units_seed.json"))
    cur = json.load(open(f"{d}/bjd_units.json"))
    newname = {}   # 인천 '26.7 개편 구 이름만 별칭으로(다른 시 · 도는 같은 동 이름이 여러 시에 있어 섞이지 않게)
    for sd, sgc, bc, sg, emd, ri in cur:
        sd, sg, emd, ri = S(sd), S(sg), S(emd), S(ri)
        if sd.startswith("인천"):
            newname.setdefault((short(sd, sg), emd, ri), set()).add(lv(sg))
    byname = {}    # 코드가 바뀐 곳(부천 구 신설 등)은 같은 시 · 도 안에서 이름으로 대표점을 찾는다
    for k, v in pts.items():
        byname.setdefault((k[:2], v[2]), []).append(k)
    out, n = {}, 0
    for sd, sgc, bc, sg, emd, ri in seed:
        sd, sg, emd, ri = S(sd), S(sg), S(emd), S(ri)
        k = sgc + bc if ri else sgc + bc[:3]
        p = pts.get(k) or (pts.get("45" + k[2:]) if k.startswith("52") else None)   # 전북 코드 변경(45 → 52, '24.1)
        if not p:
            sd2 = "45" if k.startswith("52") else k[:2]
            c = [x for x in byname.get((sd2, ri or emd), []) if len(x) == len(k)]
            if c:
                best = max(c, key=lambda x: len(__import__("os").path.commonprefix([x, sd2 + k[2:]])))
                p = pts[best]
        if not p or not emd:
            continue
        s = short(sd, sg)
        names = sorted({lv(sg)} | newname.get((s, emd, ri), set()))
        key = f"{emd} {ri}" if ri else emd
        out.setdefault(s, {}).setdefault(key, []).append([names, p[0], p[1]])
        n += 1
    # 읍 · 면 자체 대표점이 없으면 그 아래 리 대표점들의 가운데로
    for s_, T in out.items():
        acc = {}
        for key, lst in T.items():
            if " " in key:
                emd = key.split(" ")[0]
                for names, x, y in lst:
                    acc.setdefault((emd, tuple(names)), []).append((x, y))
        for (emd, names), xy in acc.items():
            if emd not in T or not any(tuple(e[0]) == names for e in T[emd]):
                T.setdefault(emd, []).append([list(names), round(sum(a for a, _ in xy) / len(xy), 5), round(sum(b for _, b in xy) / len(xy), 5)])
    json.dump(out, open("collector/bjd_pts.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("bjd_pts.json", n, "points")


if __name__ == "__main__":
    main(sys.argv[1])
