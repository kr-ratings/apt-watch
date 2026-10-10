# 아파트 분양시장 청약 · 미분양 현황 대시보드

청약홈 APT 분양공고(최근 6개월)의 공급금액 · 청약 경쟁률과 국토교통부 시·군·구별 미분양 · 준공 후 미분양을 한 화면에 보여 주는 정적 사이트입니다. 디자인은 [유동화시장 등급 · 금리 현황](https://daily-ratings.pages.dev/) · [시공사 시공능력평가 · 신용등급 현황](https://cons-ratings.pages.dev/) 대시보드와 같은 토큰을 씁니다.

사이트: https://apt-watch.pages.dev (Cloudflare Pages, 배포 폴더 `site`)

## 구성

| 경로 | 내용 |
|---|---|
| `site/index.html` | 화면 전체(HTML · CSS · JS 한 파일) |
| `site/data/subscriptions.json` | 단지별 청약 자료(주택형별 공급금액 · 경쟁률 포함) |
| `site/data/unsold.json` | 시·군·구별 미분양 · 준공 후 미분양 최근 13개월 |
| `site/data/supply.json` | 시·도별 아파트 입주(실적 · 예정) · 수요(인구×0.5%) 연도별 |
| `site/data/status.json` | 마지막 수집 시각과 출처별 성공 여부 |
| `cache/applyhome.json` | 청약홈 단지별 원자료 캐시(이미 받은 상세 · 확정된 경쟁률은 다시 받지 않음) |
| `collector/collect.py` | 수집기(청약 · 미분양) |
| `collector/supply.py` | 입주 · 수요 수집기 |
| `.github/workflows/update.yml` | 평일 07:10 KST 자동 실행 |

## 갱신 방식

1. **청약홈**: APT 분양정보 목록(모집공고 최근 6개월)을 넘기며 단지마다 '모집공고 주요정보'와 '청약 접수 경쟁률' 팝업을 읽습니다. 상세는 한 번만, 경쟁률은 당첨자 발표 2일 뒤까지 매번 다시 받습니다.
2. **통계누리**: 미분양주택현황보고의 시·군·구별 미분양현황(formId 2082)과 공사완료후 미분양현황(formId 5328)을 월별로 받습니다. 매월 말 전월분이 올라오면 다음 실행 때 반영됩니다. 광주 · 전남은 통계누리 표기대로 '전남광주'로 합칩니다.
3. **입주 · 수요**: 통계누리 '주택유형별 주택건설 준공실적(월계)'(formId 5373, 60개월씩 나눠 조회)의 아파트 사용검사 실적, 청약홈 '입주(예정)정보' 첨부 엑셀(한국부동산원 · 부동산R114 단지별 입주예정), 행정안전부 주민등록 인구(연말, CSV)를 받아 시·도별 · 연도별로 합칩니다. 예정 자료가 시작하는 달부터는 실적 대신 예정을 씁니다.
4. 한 출처가 실패하면 직전 값을 유지하고 화면 상단 수집 상태에 빨간 점으로 표시합니다.

## 수동 실행

```bash
pip install -r requirements.txt
python collector/collect.py            # 전부
python collector/collect.py unsold     # 미분양만
python collector/collect.py supply     # 입주 · 수요만
python collector/collect.py applyhome  # 청약만
```
