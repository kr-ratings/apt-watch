# 아파트 수급 · 분양시장 현황 대시보드

청약홈 APT 분양공고(최근 6개월)의 공급금액 · 청약 경쟁률과 국토교통부 시·군·구별 미분양 · 준공 후 미분양을 한 화면에 보여 주는 정적 사이트입니다. 디자인은 [유동화시장 등급 · 금리 현황](https://abs.namepf.com/) · [시공사 시공능력평가 · 신용등급 현황](https://cons.namepf.com/) 대시보드와 같은 토큰을 씁니다.

사이트: https://apt.namepf.com (Cloudflare Pages, 배포 폴더 `site`)

## 구성

| 경로 | 내용 |
|---|---|
| `site/index.html` | 화면 전체(HTML · CSS · JS 한 파일) |
| `site/data/subscriptions.json` | 단지별 청약 자료(주택형별 공급금액 · 경쟁률 포함) |
| `site/data/unsold.json` | 시·군·구별 미분양 · 준공 후 미분양 최근 13개월 |
| `site/data/supply.json` | 아파트 입주(실적 · 예정) · 수요(인구×0.5%) 연도별 — 단지 목록 기준(실적 K-apt 사용승인, 예정 부동산원·R114 임대 제외 + 청약홈 분양 공고). 시도는 `data`, 시·군·구는 `sgg`, 국토부 준공은 참고값 `molit` |
| `site/data/units.json`, `site/data/units/` | 시·도가 매월 올리는 미분양 주택 현황(업체별 = 단지별) 원문 파일과 목록. 원문은 바꾸지 않고 시·도별 최신 1개만 둠 |
| `site/data/rt/main.json`, `site/data/rt/d00~.json`, `site/data/rt/sum.json` | 실거래가 지도: 전국 · 시도 · 시군구 달별 지수(같은 단지 반복거래) · 끝 달까지 3개월 평당가(공급 환산 · 전용 · 지역별 세대수 가중), 법정동은 시도별 파일(동 단위로 확대할 때 불러옴) |
| `site/data/hm.json` | 아파트 시가총액(추정) vs M2 월별(2006-01~) |
| `site/data/status.json` | 마지막 수집 시각과 출처별 성공 여부 |
| `cache/applyhome.json` | 청약홈 단지별 원자료 캐시(이미 받은 상세 · 확정된 경쟁률은 다시 받지 않음) |
| `collector/collect.py` | 수집기(청약 · 미분양) |
| `collector/supply.py` | 입주 · 수요 수집기 |
| `collector/sgg.py` | K-apt 관리비공개의무단지 기본정보(사용승인일 · 세대수, 매주 게시), 청약홈 분양 공고 입주예정월 · 모집공고문 단지 전체 세대수(`cache/movein.json`), 행안부 전체 시군구 인구 |
| `collector/market.py` | 국토부 아파트 매매 실거래가(상세) 달별 요약(`cache/rt/YYYYMM.json.gz`, 2006-01(실거래가 공개 시작)~, 거래 한 건씩(단지 · 전용면적 · 층 · 금액) 저장, 최근 4개월은 매번 다시 받고 빠진 달 · 옛 형식 달은 한 번에 11분 안에서 이어 받음 · 변동률 지수는 같은 집 반복매매) → 실거래가 지도 자료, 한국은행 ECOS(M2 · 한국부동산원 아파트 매매 실거래가격지수, `cache/ecos.json`) + K-apt 세대수(`cache/kapt_market.json`) → 아파트 시가총액 |
| `collector/units.py` | 시·도 게시판에서 최신 '미분양 주택 현황' 게시물과 첨부 파일을 찾아 받음(서울·부산·인천·대전·울산·경기·강원·충북·충남·경북·경남). 대구는 해외 서버 접속이 자주 막혀, 실패하면 직전 파일을 두고 다음 실행에 다시 시도 |
| `.github/workflows/update.yml` | 평일 07:10 KST 자동 실행 |

## 갱신 방식

1. **청약홈**: APT 분양정보 목록(모집공고 최근 6개월)을 넘기며 단지마다 '모집공고 주요정보'와 '청약 접수 경쟁률' 팝업을 읽습니다. 상세는 한 번만, 경쟁률은 당첨자 발표 2일 뒤까지 매번 다시 받습니다.
2. **통계누리**: 미분양주택현황보고의 시·군·구별 미분양현황(formId 2082)과 공사완료후 미분양현황(formId 5328)을 월별로 받습니다. 매월 말 전월분이 올라오면 다음 실행 때 반영됩니다. 광주 · 전남은 통계누리 표기대로 '전남광주'로 합칩니다.
3. **입주 · 수요**: 통계누리 '주택유형별 주택건설 준공실적(월계)'(formId 5373, 60개월씩 나눠 조회)의 아파트 사용검사 실적, 청약홈 '입주(예정)정보' 첨부 엑셀(한국부동산원 · 부동산R114 단지별 입주예정), 행정안전부 주민등록 인구(연말, CSV)를 받아 시·도별 · 연도별로 합칩니다. 예정 자료가 시작하는 달부터는 실적 대신 예정을 씁니다.
4. **단지별 미분양 원문**: 시·도별 게시판 목록에서 최신 게시물을 찾고, 제목+첨부 주소가 바뀌었을 때만 새로 받습니다(매 실행마다 확인).
5. **실거래가 · 시가총액**: GitHub Secrets의 `DATA_GO_KR_KEY`(공공데이터포털 '국토교통부_아파트 매매 실거래가 상세 자료' 활용신청 키)가 있으면 최근 4개월 실거래를 다시 받고, 없으면 저장된 달별 자료로만 만듭니다. ECOS는 `ECOS_API_KEY`가 있으면 그 키로, 없으면 공개 견본 키(한 번에 10줄)로 받습니다. 키와 요청 주소는 로그에 남기지 않습니다.
6. 한 출처가 실패하면 직전 값을 유지하고 화면 상단 수집 상태에 빨간 점으로 표시합니다.

## 수동 실행

```bash
pip install -r requirements.txt
python collector/collect.py            # 전부
python collector/collect.py unsold     # 미분양만
python collector/collect.py supply     # 입주 · 수요만
python collector/collect.py applyhome  # 청약만
python collector/collect.py market     # 실거래가 지도 · 아파트 시가총액만(입주 · 수요가 만든 cache/kapt_market.json 필요)
```
