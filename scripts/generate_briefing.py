#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_briefing.py
매일 자동으로 실행되어 briefing.html 을 생성하는 스크립트.

원본(사용자가 참고한) 페이지의 형식을 따라, 각 섹션을
"제목 + 복사 버튼 + 스크롤 가능한 텍스트박스" 형태로 렌더링한다.
텍스트박스 안 내용은 카카오톡/문자 등에 바로 복사-붙여넣기 하기 좋은
순수 텍스트(줄바꿈 + 이모지) 포맷이다.

각 섹션은 서로 독립적으로 try/except 로 감싸, 한 섹션이 실패해도
나머지 섹션은 정상적으로 만들어지도록 설계했다.
"""

import datetime
import html
import os
import random
import re
import sys
import traceback

import requests

try:
    import feedparser
except ImportError:
    feedparser = None

KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)

WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
TODAY_STR = f"{NOW.year}년 {NOW.month}월 {NOW.day}일 {WEEKDAY_KO[NOW.weekday()]}"
UPDATED_STR = NOW.strftime("%m월 %d일 %H:%M")
YY2 = NOW.strftime("%y")  # "26"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def safe(section_name, fallback="데이터를 가져오지 못했습니다."):
    """최종 섹션(build_*) 함수를 감싸는 데코레이터.
    실패 시 fallback 문자열을 반환한다 (섹션 자체는 항상 표시됨)."""

    def deco(fn):
        def wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                if not result:
                    return fallback
                return result
            except Exception:
                print(f"[WARN] '{section_name}' 섹션 실패:", file=sys.stderr)
                traceback.print_exc()
                return fallback

        return wrapper

    return deco


def safe_none(fetch_name):
    """내부 fetch_* 헬퍼용 데코레이터. 실패 시 None을 반환한다
    (문자열이 아니라 None이어야 상위 build_* 함수의 `is None` 체크가 동작함)."""

    def deco(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                print(f"[WARN] '{fetch_name}' 가져오기 실패:", file=sys.stderr)
                traceback.print_exc()
                return None

        return wrapper

    return deco


def fmt_pct(v):
    if v is None:
        return "-"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


# ---------------------------------------------------------------------------
# 1) 오늘의 운세 — 띠별 풀텍스트 (외부 데이터 없이 날짜 기반 생성)
# ---------------------------------------------------------------------------
ZODIAC = [
    "쥐띠", "소띠", "호랑이띠", "토끼띠", "용띠", "뱀띠",
    "말띠", "양띠", "원숭이띠", "닭띠", "개띠", "돼지띠",
]
FORTUNE_LINES = [
    "생각지 못한 곳에서 좋은 소식이 들려온다.",
    "평소보다 신중한 판단이 필요한 하루다.",
    "주변 사람과의 대화에서 힌트를 얻을 수 있다.",
    "무리한 지출은 피하는 것이 좋다.",
    "작은 성취가 큰 자신감으로 이어진다.",
    "컨디션 관리에 신경 쓰면 좋은 하루가 된다.",
    "새로운 시도를 하기에 나쁘지 않은 날이다.",
    "감정적인 결정보다는 차분한 판단이 필요하다.",
    "행운을 가져다주는 사람이 가까이에 있다.",
    "작은 것에도 감사하는 마음을 가지면 좋다.",
    "쓸 만큼의 금전 유통은 가능하다.",
    "고생 끝에 낙이 온다. 힘들었던 문제도 해결될 것이다.",
    "귀인의 도움으로 막힌 일이 풀린다.",
    "건강 관리에 소홀하지 않도록 주의가 필요하다.",
    "가족과의 시간에서 안정을 찾을 수 있다.",
]


def _zodiac_index(year):
    # 2020년 = 쥐띠(경자년) 기준
    return (year - 2020) % 12


@safe("운세")
def build_fortune():
    seed = int(NOW.strftime("%Y%m%d"))
    rng = random.Random(seed)
    lines = [f"오늘의 운세, {NOW.month}월 {NOW.day}일", ""]
    for i, animal in enumerate(ZODIAC):
        lines.append(f"<{animal}>")
        # 최근 4세대(12년 간격) 출생년도
        gen_years = []
        y = NOW.year
        while len(gen_years) < 4:
            y -= 1
            if _zodiac_index(y) == i:
                gen_years.append(y % 100)
                y -= 11  # 다음 루프에서 -1 되어 총 -12
        sentence_parts = []
        for gy in gen_years:
            sentence_parts.append(f"{gy:02d}년생 {rng.choice(FORTUNE_LINES)}")
        lines.append(" ".join(sentence_parts))
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# 2) 날씨 — Open-Meteo 공개 API (공식, 키 불필요, 안정적)
# ---------------------------------------------------------------------------
CITIES = [
    ("서울", 37.5665, 126.9780),
    ("인천", 37.4563, 126.7052),
    ("광주", 35.1595, 126.8526),
    ("대구", 35.8714, 128.6014),
    ("부산", 35.1796, 129.0756),
    ("울산", 35.5384, 129.3114),
    ("창원", 35.2280, 128.6811),
    ("제주", 33.4996, 126.5312),
]

WEATHER_EMOJI = {
    range(0, 1): "☀️",
    range(1, 4): "🌤️",
    range(45, 49): "🌫️",
    range(51, 68): "🌧️",
    range(71, 78): "🌨️",
    range(80, 83): "🌦️",
    range(95, 100): "⛈️",
}


def weather_emoji(code):
    for r, e in WEATHER_EMOJI.items():
        if code in r:
            return e
    return "⛅"


@safe("날씨")
def build_weather():
    lines = [TODAY_STR, "", "▷ 지역별 날씨전망 ▷", ""]
    ok = False
    for name, lat, lon in CITIES:
        try:
            resp = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "weathercode,temperature_2m_max,temperature_2m_min",
                    "timezone": "Asia/Seoul",
                    "forecast_days": 1,
                },
                timeout=10,
            )
            resp.raise_for_status()
            d = resp.json()["daily"]
            code_now = d["weathercode"][0]
            tmax = round(d["temperature_2m_max"][0])
            tmin = round(d["temperature_2m_min"][0])
            emoji = weather_emoji(code_now)
            lines.append(f"☆{name}({emoji})  {tmin}℃ ~ {tmax}℃")
            ok = True
        except Exception:
            continue
    if not ok:
        return None
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3) 오늘의 퀵뉴스 — 연합뉴스 RSS를 카테고리 이모지로 묶은 통합 텍스트
# ---------------------------------------------------------------------------
NEWS_FEEDS = [
    ("🏛", "정치", "https://www.yna.co.kr/rss/politics.xml"),
    ("💰", "경제", "https://www.yna.co.kr/rss/economy.xml"),
    ("🏙", "사회", "https://www.yna.co.kr/rss/society.xml"),
    ("🌍", "국제", "https://www.yna.co.kr/rss/international.xml"),
]


@safe("퀵뉴스")
def build_quicknews(limit_per_cat=3):
    if feedparser is None:
        return None
    lines = [f"{YY2}년 {NOW.month}월 {NOW.day}일 {WEEKDAY_KO[NOW.weekday()]} 오늘의 퀵뉴스⚡", ""]
    ok = False
    for emoji, category, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
            entries = feed.entries[:limit_per_cat]
            if not entries:
                continue
            lines.append(f"{emoji} ({category}) " + " / ".join(
                e.get("title", "").strip() for e in entries
            ))
            ok = True
        except Exception:
            continue
    if not ok:
        return None
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4) 청약 소식 — best-effort (안정적인 무료 소스가 마땅치 않아 뉴스 기반 대체)
# ---------------------------------------------------------------------------
@safe("청약 소식")
def build_subscription():
    if feedparser is None:
        return None
    feed = feedparser.parse("https://www.yna.co.kr/rss/economy.xml")
    items = [e for e in feed.entries if "청약" in e.get("title", "") or "분양" in e.get("title", "")]
    if not items:
        return None
    lines = [f"{YY2}년 {NOW.month}월 {NOW.day}일 {WEEKDAY_KO[NOW.weekday()]} 청약 소식", "", "📝 관련 뉴스"]
    for e in items[:5]:
        lines.append(f"- {e.get('title', '').strip()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5) 부동산 주간 시세동향 — best-effort (부동산원 공식 API는 키가 필요해
#    현재는 관련 뉴스로 대체. 추후 공공데이터포털 키 연동 시 실제 수치로 교체 가능)
# ---------------------------------------------------------------------------
@safe("부동산 주간 시세동향")
def build_realestate_trend():
    if feedparser is None:
        return None
    feed = feedparser.parse("https://www.yna.co.kr/rss/economy.xml")
    keywords = ["아파트", "부동산", "전세", "매매가", "집값"]
    items = [e for e in feed.entries if any(k in e.get("title", "") for k in keywords)]
    if not items:
        return None
    lines = [f"{YY2}년 {NOW.month}월 {NOW.day}일 {WEEKDAY_KO[NOW.weekday()]} 부동산 주간 시세동향", "", "📈 관련 뉴스"]
    for e in items[:6]:
        lines.append(f"- {e.get('title', '').strip()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6) 기름값·환율
# ---------------------------------------------------------------------------
@safe_none("환율")
def fetch_fx():
    resp = requests.get(
        "https://api.frankfurter.dev/v1/latest",
        params={"base": "USD", "symbols": "KRW"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return round(data["rates"]["KRW"], 2)


@safe_none("기름값")
def fetch_gas_price():
    # best-effort: 오피넷 메인 페이지 텍스트에서 가격 패턴 추출 (API 키 불필요)
    resp = requests.get("https://www.opinet.co.kr/user/main/mainView.do", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    text = resp.text
    gasoline = re.search(r"휘발유[^0-9]{0,20}([\d,]{4,7})", text)
    diesel = re.search(r"경유[^0-9]{0,20}([\d,]{4,7})", text)
    if not gasoline and not diesel:
        return None
    return {
        "gasoline": gasoline.group(1) if gasoline else "-",
        "diesel": diesel.group(1) if diesel else "-",
    }


@safe("기름값·환율")
def build_gas_fx():
    fx = fetch_fx()
    gas = fetch_gas_price()
    if fx is None and gas is None:
        return None
    lines = [f"{YY2}년 {NOW.month}월 {NOW.day}일 {WEEKDAY_KO[NOW.weekday()]} 기름값·환율", ""]
    if gas:
        lines.append("⛽ 전국 평균 기름값")
        lines.append(f"휘발유 {gas['gasoline']}원/L")
        lines.append(f"경유 {gas['diesel']}원/L")
        lines.append("")
    if fx:
        lines.append("💱 환율")
        lines.append(f"미국 USD {fx}원")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7) 금·은·코인
# ---------------------------------------------------------------------------
@safe_none("금은시세")
def fetch_gold_silver():
    # best-effort: 네이버 금융 국제 금 시세 페이지 텍스트에서 패턴 추출
    resp = requests.get("https://finance.naver.com/marketindex/", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    text = resp.text
    gold = re.search(r"국내\s*금[^0-9]{0,20}([\d,]{5,8})", text)
    if not gold:
        return None
    return {"domestic_gold": gold.group(1)}


@safe_none("코인시세")
def fetch_crypto():
    resp = requests.get(
        "https://api.upbit.com/v1/ticker",
        params={"markets": "KRW-BTC,KRW-ETH"},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    out = {}
    for row in data:
        market = row["market"].replace("KRW-", "")
        out[market] = {
            "price": row["trade_price"],
            "change_rate": round(row["signed_change_rate"] * 100, 2),
        }
    return out


@safe("금·은·코인")
def build_metals_coin():
    gold = fetch_gold_silver()
    coin = fetch_crypto()
    if gold is None and coin is None:
        return None
    lines = [f"{YY2}년 {NOW.month}월 {NOW.day}일 {WEEKDAY_KO[NOW.weekday()]} 금·은·코인", ""]
    if gold:
        lines.append("🥇 금 시세")
        lines.append(f"국내 금 {gold['domestic_gold']}원/g")
        lines.append("")
    if coin:
        lines.append("🪙 코인 시세")
        for name, v in coin.items():
            lines.append(f"{name} {v['price']:,.0f}원 ({fmt_pct(v['change_rate'])})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8) 주간 베스트셀러 — 알라딘 Open API (무료지만 TTB 키 필요)
#    저장소 Secrets에 ALADIN_TTB_KEY 를 등록하면 자동으로 채워진다.
# ---------------------------------------------------------------------------
@safe("주간 베스트셀러", fallback="알라딘 API 키(ALADIN_TTB_KEY)가 설정되지 않아 표시할 수 없습니다.\n"
                                 "https://blog.aladin.co.kr/openapi 에서 무료로 발급받아 저장소 Settings > "
                                 "Secrets and variables > Actions 에 등록하면 자동으로 채워집니다.")
def build_bestseller():
    key = os.environ.get("ALADIN_TTB_KEY")
    if not key:
        return None
    resp = requests.get(
        "https://www.aladin.co.kr/ttb/api/ItemList.aspx",
        params={
            "ttbkey": key,
            "QueryType": "Bestseller",
            "MaxResults": 10,
            "start": 1,
            "SearchTarget": "Book",
            "output": "js",
            "Version": "20131101",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    items = data.get("item", [])
    if not items:
        return None
    lines = [f"{YY2}년 {NOW.month}월 {NOW.day}일 {WEEKDAY_KO[NOW.weekday()]} 주간 베스트셀러", "", "📚 알라딘 주간 베스트셀러 TOP 10", ""]
    for it in items[:10]:
        lines.append(f"{it.get('bestRank', '')}. {it.get('title', '')} - {it.get('author', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML 렌더링 — 섹션 = 제목바(이모지+제목+복사버튼) + 스크롤 텍스트박스
# ---------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s)) if s is not None else ""


def render_section(section_id, emoji, title, body_text):
    return f"""
  <section class="card">
    <div class="card-head">
      <h2>{emoji} {esc(title)}</h2>
      <button class="copy-btn" onclick="copySection('{section_id}')">복사</button>
    </div>
    <div class="textbox" id="{section_id}">{esc(body_text)}</div>
  </section>"""


PAGE_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>오늘의 브리핑 - {today}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin:0; background:#f4f5f7; color:#1a1a1a; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; }}
  .wrap {{ max-width:900px; margin:0 auto; padding:20px 16px 64px; }}
  header {{ text-align:center; margin-bottom:20px; }}
  header h1 {{ font-size:1.4rem; margin:0 0 4px; }}
  header .updated {{ color:#666; font-size:0.85rem; }}
  .card {{ background:#fff; border:1px solid #e2e2e2; border-radius:10px; padding:14px 16px; margin-bottom:14px; }}
  .card-head {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }}
  .card-head h2 {{ margin:0; font-size:1.05rem; }}
  .copy-btn {{ background:#4a6cf7; color:#fff; border:none; border-radius:6px; padding:6px 14px; font-size:0.85rem; cursor:pointer; }}
  .copy-btn:active {{ background:#3a5ce0; }}
  .textbox {{ white-space:pre-wrap; word-break:break-word; max-height:180px; overflow-y:auto; border:1px solid #e2e2e2; border-radius:6px; padding:10px 12px; font-size:0.88rem; line-height:1.5; background:#fafafa; }}
  footer {{ text-align:center; color:#8a8f98; font-size:0.8rem; margin-top:20px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>📅 {today}</h1>
    <div class="updated">🕐 마지막 업데이트: {updated}</div>
  </header>
  {sections}
  <footer>daily-briefing · 자동 생성</footer>
</div>
<script>
function copySection(id) {{
  const el = document.getElementById(id);
  const text = el.innerText;
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
  }} else {{
    fallbackCopy(text);
  }}
}}
function fallbackCopy(text) {{
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {{ document.execCommand('copy'); }} catch (e) {{}}
  document.body.removeChild(ta);
}}
</script>
</body>
</html>
"""


def build_page():
    sections_data = [
        ("fortune", "🐭", "오늘의 운세", build_fortune()),
        ("weather", "🌤", "날씨", build_weather()),
        ("quicknews", "⚡", "오늘의 퀵뉴스", build_quicknews()),
        ("subscription", "🏗", "청약 소식", build_subscription()),
        ("realestate", "📈", "부동산 주간 시세동향", build_realestate_trend()),
        ("gasfx", "⛽", "기름값·환율", build_gas_fx()),
        ("metalscoin", "🥇", "금·은·코인", build_metals_coin()),
        ("bestseller", "📚", "주간 베스트셀러", build_bestseller()),
    ]
    sections_html = "".join(
        render_section(sid, emoji, title, body) for sid, emoji, title, body in sections_data
    )
    return PAGE_TEMPLATE.format(
        today=esc(TODAY_STR),
        updated=esc(UPDATED_STR),
        sections=sections_html,
    )


def main():
    page = build_page()
    with open("briefing.html", "w", encoding="utf-8") as f:
        f.write(page)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(page)
    print("briefing.html / index.html 생성 완료:", TODAY_STR)


if __name__ == "__main__":
    main()
