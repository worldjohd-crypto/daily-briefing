# daily-briefing

매일 자동으로 갱신되는 종합 브리핑 페이지 (운세 / 날씨 / 뉴스 / 금융지표 / 환율·금값 / 베스트셀러 / 전국 부동산 시세동향).

## 구조

- `scripts/generate_briefing.py` — 데이터 수집 + HTML 생성 (아래 12개 섹션)
- `.github/workflows/daily.yml` — 매일 23:50(KST) 자동 실행, 결과를 커밋
- `briefing.html`, `index.html` — 생성된 결과 (GitHub Pages로 서빙)
- `fx-cache.json`, `rone-cache.json` — 외부 API 실패 시 이전 값을 재사용하기 위한 캐시 (자동 생성/커밋)

## 섹션 구성

1. 오늘의 운세
2. 날씨
3. 간추린 뉴스 (증시 지표 포함)
4. 구독 서비스 안내
5. 트렌드
6. 유가/환율
7. 금속/코인
8. 📚 주간 베스트셀러 (알라딘)
9. 📈 부동산 주간 시세동향 (한국부동산원 R-ONE)
10. 세계 뉴스
11. 금융 뉴스
12. AI 뉴스

> 참고: 원본 사이트에 있는 "정책분석" LLM 페르소나 6개 섹션은 이번 이식에 포함되지 않았습니다. 별도의 Claude 생성 단계가 필요한 부분이라 프롬프트/로직을 알 수 없어 제외했습니다.

## 필요한 GitHub Actions Secrets

리포지토리 Settings → Secrets and variables → Actions 에서 등록합니다.

| Secret | 필수 여부 | 용도 | 발급처 |
|---|---|---|---|
| `RONE_KEY` | 있어야 부동산 섹션이 정상 표시됨 (없으면 안내 문구로 대체) | 부동산 주간 시세동향 | [한국부동산원 R-ONE](https://www.reb.or.kr/r-one) 무료 회원가입 후 Open API 키 발급 |
| `ALADIN_TTB_KEY` | 선택 (권장) | 베스트셀러 — 알라딘 페이지 직접 스크래핑이 1차 시도이며, GitHub Actions IP가 차단될 경우를 대비한 대체 경로 | [알라딘 TTB API](https://www.aladin.co.kr/ttb/apiguide.aspx) 무료 신청 |

키가 없어도 워크플로우 자체는 정상적으로 돌아가며, 해당 섹션만 대체 문구(또는 캐시된 이전 값)로 표시됩니다.

## 로컬 실행

```bash
pip install -r requirements.txt
python scripts/generate_briefing.py
```

필요 시 환경변수로 키를 넘겨줍니다:

```bash
RONE_KEY=xxx ALADIN_TTB_KEY=xxx python scripts/generate_briefing.py
```
