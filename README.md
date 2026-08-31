# daily-briefing

매일 자동으로 갱신되는 종합 브리핑 페이지 (운세 / 날씨 / 뉴스 / 금융지표 / 환율·금값 / 베스트셀러 / 전국 부동산 시세동향 / AI 논평).

## 구조

- `scripts/generate_briefing.py` — 데이터 수집 + HTML 생성 (아래 18개 섹션)
- `.github/workflows/daily.yml` — 매일 06:30~21:30(KST) 사이 1시간 간격(하루 16회) 자동 실행, 결과를 커밋
- `briefing.html`, `index.html` — 생성된 결과 (GitHub Pages로 서빙)
- `fx-cache.json`, `rone-cache.json` — 외부 API 실패 시 이전 값을 재사용하기 위한 캐시 (자동 생성/커밋)
- `comment-cache.json` — AI 논평 6개의 마지막 생성 결과 캐시 (자동 생성/커밋, 아래 "AI 논평 새로 생성하기" 참고)

## 섹션 구성

1. 오늘의 운세
2. 날씨
3. 오늘의 퀵뉴스 (증시 지표 포함)
4. 🏛️ 정치 논평 (AI)
5. 💹 경제 논평 (AI)
6. 청약 소식
7. 📈 부동산 주간 시세동향 (한국부동산원 R-ONE)
8. 유가·환율
9. 금·은·코인
10. 📚 주간 베스트셀러 (알라딘)
11. 부동산 뉴스
12. 🏠 부동산 시장 논평 (AI)
13. 세계 뉴스
14. 🌐 국제정세 논평 (AI)
15. 금융 뉴스
16. 🏦 금융시장 논평 (AI)
17. AI 뉴스
18. 🤖 AI·테크 논평 (AI)

"(AI)" 표시가 붙은 6개 논평 섹션은 원본 사이트의 "정책분석" 코너를 그대로 이식한 것이 아니라, 같은 자리를 대체하는 새 기능입니다. 그날 수집한 뉴스 제목/요약을 재료로 Claude API(Anthropic)가 짧은 전문가 페르소나 논평(3~4문장)을 생성합니다. `ANTHROPIC_API_KEY`가 없으면 이 6개 섹션만 안내 문구로 대체되고, 나머지 12개 섹션에는 전혀 영향이 없습니다.

### AI 논평 새로 생성하기 (비용 발생 구간)

이 6개 섹션만 Claude API 호출 비용이 듭니다. 비용을 통제하기 위해 **평소 매시간 자동 실행에서는 API를 새로 호출하지 않고, `comment-cache.json`에 저장된 마지막 생성 결과를 그대로 재사용**합니다(무료). 나머지 12개 무료 섹션(날씨, 뉴스, 시세동향 등)은 지금처럼 매시간 그대로 갱신됩니다.

논평을 새로 생성하고 싶을 때는, **브리핑 페이지 상단의 "▶ AI 논평 새로 받기" 버튼**을 누르면 GitHub Actions의 실행 화면으로 바로 이동합니다(GitHub 로그인 필요). 거기서 아래 순서로 실행하면 됩니다.

1. (버튼을 눌러 이동했다면 이미 와 있는) **Actions** 탭 → **Daily Briefing** 워크플로우
2. **Run workflow** 버튼 클릭
3. **"AI 논평 6개 새로 생성"** 체크박스를 체크
4. **Run workflow**로 실행

※ 페이지 안의 버튼을 누르는 즉시 논평이 생성되는 구조는 아닙니다. 이 사이트는 서버 없이 정적 파일만 올라가는 GitHub Pages라서, 버튼이 브라우저에서 곧바로 유료 API를 호출하게 만들려면 인증 토큰을 페이지 코드 안에 넣어야 하는데, 그러면 사이트를 보는 누구나 그 토큰을 볼 수 있게 되어 보안상 위험합니다. 그래서 버튼은 "GitHub의 실행 화면으로 안내"하는 역할까지만 하고, 실제 실행(체크박스 체크 + Run workflow)은 GitHub 로그인 상태에서 한 번 더 눌러줘야 합니다.

이때만 Claude API가 실제로 호출되어 6개 논평이 새로 생성되고, 그 결과가 `comment-cache.json`에 저장됩니다. 체크박스를 체크하지 않고 수동 실행하거나 예약된 시간에 자동 실행되면 캐시된 논평이 그대로 표시됩니다(각 논평 제목 옆에 `· MM월 DD일 HH:MM 생성`으로 마지막 생성 시각이 표시됩니다).

## 필요한 GitHub Actions Secrets

리포지토리 Settings → Secrets and variables → Actions 에서 등록합니다.

| Secret | 필수 여부 | 용도 | 발급처 |
|---|---|---|---|
| `RONE_KEY` | 있어야 부동산 시세동향 섹션이 정상 표시됨 (없으면 안내 문구로 대체) | 부동산 주간 시세동향 | [한국부동산원 R-ONE](https://www.reb.or.kr/r-one) 무료 회원가입 후 Open API 키 발급 |
| `ALADIN_TTB_KEY` | 선택 (권장) | 베스트셀러 — 알라딘 페이지 직접 스크래핑이 1차 시도이며, GitHub Actions IP가 차단될 경우를 대비한 대체 경로 | [알라딘 TTB API](https://www.aladin.co.kr/ttb/apiguide.aspx) 무료 신청 |
| `ANTHROPIC_API_KEY` | 있어야 6개 AI 논평 섹션이 정상 표시됨 (없으면 안내 문구로 대체) | 정치/경제/부동산/국제/금융/AI 논평 생성 | [console.anthropic.com](https://console.anthropic.com) 에서 발급. 유료이며, 기본 모델(Claude Haiku)로 하루 6번 짧은 논평을 생성하는 정도라 실행당 비용은 매우 적음 |

키가 없어도 워크플로우 자체는 정상적으로 돌아가며, 해당 섹션만 대체 문구(또는 캐시된 이전 값)로 표시됩니다.

선택: `ANTHROPIC_MODEL` 시크릿(또는 변수)으로 기본 모델(`claude-3-5-haiku-latest`)을 다른 모델로 바꿀 수 있습니다.

## 로컬 실행

```bash
pip install -r requirements.txt
python scripts/generate_briefing.py
```

필요 시 환경변수로 키를 넘겨줍니다:

```bash
RONE_KEY=xxx ALADIN_TTB_KEY=xxx ANTHROPIC_API_KEY=xxx python scripts/generate_briefing.py
```
