"""청약 지도용 정적 자료(site/data/map.json)를 만든다. 행정구역 개편 때만 다시 실행하면 된다.

입력(공개 자료):
  1) 행정동 경계 : github.com/vuski/admdongkor  HangJeongDong_verYYYYMMDD.geojson (통계청 SGIS 공공누리 1유형 · CC BY 4.0)
  2) 시 · 도 윤곽 : dc-watch/site/data/geo.json (같은 행정동 경계 + Natural Earth로 만든 투영 경로를 그대로 쓴다)
출력:
  site/data/map.json  {proj, sido, sgg(수도권 경계), lab, ul, nk, pts{"시도|시군구": [x, y]}, poly{"시도|시군구": 경계 path}, sa{"시도|시군구": √넓이}}
  - 시 · 군 · 구 점은 그 지역 가장 큰 땅덩이 안쪽의 대표점(바다에 찍히지 않음)
  - 구가 있는 시(수원 · 성남 · 청주 · 천안 · 포항 · 창원 · 전주 · 화성 등)는 시로 합친다(청약홈 · 통계누리 표기와 같게)
  - 전남광주통합특별시는 구 → 광주, 시 · 군 → 전남으로 나눈다(청약홈 시 · 도 표기와 같게)
  - poly: 지도 색칠용 시 · 군 · 구 경계(같은 투영 좌표를 1.6 단위로 단순화 · 정수 상대좌표). 넓은 곳부터 적어 안에 든 지역이 위에 그려진다
  - sa: 경계 넓이의 제곱근(번짐 크기에 쓴다)
사용: python3 tools/make_map.py <admdongkor.geojson> <dc-watch/site/data/geo.json>
"""
import json, math, re, sys
from shapely.geometry import Polygon
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely.ops import unary_union

SHORT = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "대전광역시": "대전", "울산광역시": "울산",
         "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북",
         "광주광역시": "광주", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주"}


def main(adm, dcgeo):
    geo = json.load(open(dcgeo, encoding="utf-8"))
    pj = geo["proj"]
    P = lambda lon, lat: ((lon - pj["lon0"]) * pj["kx"], (pj["lat0"] - lat) * 1000)
    groups = {}
    for f in json.load(open(adm, encoding="utf-8"))["features"]:
        p = f["properties"]
        sd, sg = p["sidonm"], p["sggnm"]
        if sd.endswith("통합특별시"):            # 전남광주통합특별시
            short = "광주" if sg.endswith("구") else "전남"
        else:
            short = SHORT.get(sd, sd)
        lv = re.sub(r"^(.+?시).+구$", r"\1", sg)    # 수원시장안구 → 수원시
        if short == "세종":
            lv = "세종시"
        groups.setdefault(f"{short}|{lv}", []).append(shape(f["geometry"]).buffer(0))
    pts, poly, area = {}, {}, {}
    proj = lambda g: _proj(g, P)
    for k, gs in groups.items():
        u = unary_union(gs)
        q = proj(u).simplify(1.6, preserve_topology=True)
        poly[k], area[k] = _path(q), q.area
        parts = list(u.geoms) if isinstance(u, MultiPolygon) else [u]
        big = max(parts, key=lambda q: q.area)
        c = big.representative_point()
        x, y = P(c.x, c.y)
        pts[k] = [round(x), round(y)]
    out = {k: geo[k] for k in ("proj", "sido", "sgg", "lab", "ul", "nk") if k in geo}
    out["pts"] = dict(sorted(pts.items()))
    out["poly"] = {k: poly[k] for k in sorted(poly, key=lambda k: -area[k])}
    out["sa"] = {k: round(area[k] ** 0.5, 1) for k in sorted(area)}
    out["src"] = adm.split("/")[-1]
    json.dump(out, open("site/data/map.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("map.json", len(json.dumps(out, ensure_ascii=False)), "bytes ·", len(pts), "시군구 점")


def _proj(g, P):
    from shapely.ops import transform
    return transform(lambda x, y, z=None: P(x, y), g)


def _path(geom) -> str:
    """다각형 → SVG path(외곽 + 넓은 구멍, 정수 상대좌표). 아주 작은 섬은 뺀다(가장 큰 땅덩이는 남김)."""
    parts = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    big = max(parts, key=lambda q: q.area)
    out = []
    for q in parts:
        if q.area < 6 and q is not big:
            continue
        for ring in [q.exterior, *[r for r in q.interiors if Polygon(r).area >= 6]]:
            cs = [(round(x), round(y)) for x, y in ring.coords]
            ded = [cs[0]]
            for c in cs[1:]:
                if c != ded[-1]:
                    ded.append(c)
            if len(ded) < 4:
                continue
            s = f"M{ded[0][0]} {ded[0][1]}l" + " ".join(f"{x - px} {y - py}" for (x, y), (px, py) in zip(ded[1:-1], ded[:-2])) + "z"
            out.append(s)
    return "".join(out)


if __name__ == "__main__":
    main(*sys.argv[1:3])
