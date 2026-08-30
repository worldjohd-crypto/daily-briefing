# daily-briefing

매일 자동으로 갱신되는 종합 브리핑 페이지 (운세 / 뉴스 / 금융지표 / 전국 부동산 뉴스).

## 구조

- `scripts/generate_briefing.py` — 데이터 수집 + HTML 생성
- `.github/workflows/daily.yml` — 매일 23:50(KST) 자동 실행, 결과를 커밋
- `briefing.html`, `index.html` — 생성된 결과 (GitHub Pages로 서빙)

## 로컬 실행

```bash
pip install -r requirements.txt
python scripts/generate_briefing.py
```

## 다음 단계 (아직 미포함)

- 날씨 (기상청 공공데이터포털 API 키 필요)
- 베스트셀러 (알라딘 API 키 필요)
- 부동산 매매가/전세가 수치 데이터 (네이버 부동산 내부 API, 구조 확인 후 추가 예정)
