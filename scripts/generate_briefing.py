#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_briefing.py
매일 자동으로 실행되어 briefing.html 을 생성하는 스크립트.
각 섹션은 서로 독립적으로 try/except 로 감싸, 한 섹션이 실패해도
나머지 섹션은 정상적으로 만들어지도록 설계했다.

이 파일은 GitHub Actions 러너(인터넷 접속 가능)에서 실행되는 것을
전제로 작성됨. 로컬/샌드박스에서 네트워크가 막혀 있으면 각 섹션은
"데이터를 가져오지 못했습니다" 상태로 표시되고 스크립트는 정상 종료된다.
"""

import datetime
import html
import json
import random
import sys
import traceback

import requests

try:
    import feedparser
except ImportError:
    feedparser = None

KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
TODAY_STR = NOW.strftime("%Y년 %m월 %d일 (%a)")
UPDATED_STR = NOW.strftime("%m월 %d일 %H:%M")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def safe(section_name):
    """섹션 함수를 감싸서 예외가 나도 전체 빌드가 죽지 않게 하는 데코레이터"""

    def deco(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                print(f"[WARN] '{section_name}' 섹션 실패:", file=sys.stderr)
                traceback.print_exc()
                return None

        return wrapper

    return deco


# ---------------------------------------------------------------------------
# 1) 운세 — 외부 데이터 없이 날짜 기반으로 생성 (항상 성공)
# ---------------------------------------------------------------------------
ZODIAC = [
    "쥐띠", "소띠", "호랑이띠", "토끼띠", "용띠", "뱀띠",
    "말띠", "양띠", "원숭이띠", "닭띠", "개띠", "돼지띠",
]
FORTUNE_LINES = [
    "생각지 못한 곳에서 좋은 소식이 들려옵니다.",
    "평소보다 신중한 판단이 필요한 하루입니다.",
    "주변 사람과의 대화에서 힌트를 얻을 수 있어요.",
    "무리한 지출은 피하는 것이 좋습니다.",
    "작은 성취가 큰 자신감으로 이어집니다.",
    "컨디션 관리에 신경 쓰면 좋은 하루가 됩니다.",
    "새로운 시도를 하기에 나쁘지 않은 날입니다.",
    "감정적인 결정보다는 차분한 판단이 필요합니다.",
]


@safe("운세")
def build_fortune():
    seed = int(NOW.strftime("%Y%m%d"))
    rng = random.Random(seed)
    items = []
    for animal in ZODIAC:
        money = rng.randint(1, 5)
        health = rng.randint(1, 5)
        love = rng.randint(1, 5)
        line = rng.choice(FORTUNE_LINES)
        items.append(
            {
                "animal": animal,
                "money": money,
                "health": health,
                "love": love,
                "line": line,
            }
        )
    return items


# ---------------------------------------------------------------------------
# 2) 뉴스 — 연합뉴스 RSS (표준 RSS라 페이지 구조 변경에 안전함)
# ---------------------------------------------------------------------------
NEWS_FEEDS = {
    "정치": "https://www.yna.co.kr/rss/politics.xml",
    "경제": "https://www.yna.co.kr/rss/economy.xml",
    "사회": "https://www.yna.co.kr/rss/society.xml",
    "국제": "https://www.yna.co.kr/rss/international.xml",
}


@safe("뉴스")
def build_news(limit=5):
    if feedparser is None:
        return None
    result = {}
    for category, url in NEWS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            entries = feed.entries[:limit]
            result[category] = [
                {"title": e.get("title", "").strip(), "link": e.get("link", "")}
                for e in entries
            ]
        except Exception:
            result[category] = []
    if not any(result.values()):
        return None
    return result


# ---------------------------------------------------------------------------
# 3) 금융지표
#    - 코인시세: 업비트 공개 API (공식, 안정적, 키 불필요)
#    - 환율: Frankfurter 공개 API (공식, 안정적, 키 불필요)
#    - 코스피/금: 네이버 페이지 best-effort 파싱 (구조가 바뀌면 실패할 수 있음)
# ---------------------------------------------------------------------------
@safe("코인시세")
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


@safe("환율")
def fetch_fx():
    resp = requests.get(
        "https://api.frankfurter.dev/v1/latest",
        params={"base": "USD", "symbols": "KRW"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"usd_krw": round(data["rates"]["KRW"], 2), "date": data.get("date")}


@safe("코스피")
def fetch_kospi():
    # best-effort: 네이버 모바일 증권 API (구조가 바뀌면 None 반환)
    resp = requests.get(
        "https://m.stock.naver.com/api/index/KOSPI/basic",
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "value": data.get("closePrice") or data.get("now") or "-",
        "change": data.get("compareToPreviousClosePrice", "-"),
    }


@safe("금융지표")
def build_market():
    crypto = fetch_crypto()
    fx = fetch_fx()
    kospi = fetch_kospi()
    if crypto is None and fx is None and kospi is None:
        return None
    return {"crypto": crypto, "fx": fx, "kospi": kospi}


# ---------------------------------------------------------------------------
# 4) 부동산 — 전국 부동산 뉴스 (RSS 기반이라 안정적)
#    가격 동향(매매가/전세가)은 네이버 부동산 내부 API가 비공식이라
#    별도 검증 없이는 신뢰하기 어려워, 1단계에서는 "전국 부동산 뉴스"로 시작.
# ---------------------------------------------------------------------------
REALESTATE_FEED = "https://www.yna.co.kr/rss/economy.xml"
REALESTATE_KEYWORDS = ["아파트", "부동산", "전세", "매매", "청약", "분양"]


@safe("부동산")
def build_realestate(limit=8):
    if feedparser is None:
        return None
    feed = feedparser.parse(REALESTATE_FEED)
    items = []
    for e in feed.entries:
        title = e.get("title", "")
        if any(k in title for k in REALESTATE_KEYWORDS):
            items.append({"title": title.strip(), "link": e.get("link", "")})
        if len(items) >= limit:
            break
    if not items:
        return None
    return items


# ---------------------------------------------------------------------------
# HTML 렌더링
# ---------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s)) if s is not None else ""


def render_fortune(items):
    if not items:
        return "<p class='empty'>운세 데이터를 가져오지 못했습니다.</p>"
    rows = "".join(
        f"<tr><td>{esc(it['animal'])}</td>"
        f"<td>💰{'★' * it['money']}{'☆' * (5 - it['money'])}</td>"
        f"<td>❤️{'★' * it['love']}{'☆' * (5 - it['love'])}</td>"
        f"<td>💪{'★' * it['health']}{'☆' * (5 - it['health'])}</td>"
        f"<td>{esc(it['line'])}</td></tr>"
        for it in items
    )
    return (
        "<table class='fortune-table'><thead><tr>"
        "<th>띠</th><th>금전</th><th>애정</th><th>건강</th><th>한줄</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def render_news(news):
    if not news:
        return "<p class='empty'>뉴스 데이터를 가져오지 못했습니다.</p>"
    blocks = []
    for category, items in news.items():
        if not items:
            continue
        lis = "".join(
            f"<li><a href='{esc(it['link'])}' target='_blank' rel='noopener'>{esc(it['title'])}</a></li>"
            for it in items
        )
        blocks.append(f"<h3>{esc(category)}</h3><ul>{lis}</ul>")
    return "".join(blocks) if blocks else "<p class='empty'>뉴스 데이터를 가져오지 못했습니다.</p>"


def render_market(market):
    if not market:
        return "<p class='empty'>금융지표 데이터를 가져오지 못했습니다.</p>"
    parts = []
    fx = market.get("fx")
    if fx:
        parts.append(f"<div class='stat'><span class='label'>USD/KRW</span><span class='value'>{esc(fx['usd_krw'])}원</span></div>")
    kospi = market.get("kospi")
    if kospi:
        parts.append(f"<div class='stat'><span class='label'>코스피</span><span class='value'>{esc(kospi['value'])}</span></div>")
    crypto = market.get("crypto")
    if crypto:
        for coin, v in crypto.items():
            sign = "+" if v["change_rate"] >= 0 else ""
            parts.append(
                f"<div class='stat'><span class='label'>{esc(coin)}</span>"
                f"<span class='value'>{esc(v['price']):}원 ({sign}{esc(v['change_rate'])}%)</span></div>"
            )
    return "".join(parts) if parts else "<p class='empty'>금융지표 데이터를 가져오지 못했습니다.</p>"


def render_realestate(items):
    if not items:
        return "<p class='empty'>부동산 뉴스를 가져오지 못했습니다.</p>"
    lis = "".join(
        f"<li><a href='{esc(it['link'])}' target='_blank' rel='noopener'>{esc(it['title'])}</a></li>"
        for it in items
    )
    return f"<ul>{lis}</ul>"


PAGE_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>오늘의 브리핑 - {today}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin:0; background:#f4f5f7; color:#1f2328; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; }}
  .wrap {{ max-width:860px; margin:0 auto; padding:24px 16px 64px; }}
  header {{ text-align:center; margin-bottom:24px; }}
  header h1 {{ font-size:1.6rem; margin:0 0 4px; }}
  header .date {{ color:#57606a; font-size:0.95rem; }}
  section {{ background:#fff; border:1px solid #e4e7eb; border-radius:12px; padding:18px 20px; margin-bottom:16px; }}
  section h2 {{ margin:0 0 12px; font-size:1.1rem; display:flex; align-items:center; gap:6px; }}
  table.fortune-table {{ width:100%; border-collapse:collapse; font-size:0.85rem; }}
  table.fortune-table th, table.fortune-table td {{ padding:6px 4px; border-bottom:1px solid #eee; text-align:left; }}
  ul {{ margin:4px 0 12px; padding-left:18px; }}
  li {{ margin-bottom:4px; font-size:0.92rem; }}
  a {{ color:#1a73e8; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .stat {{ display:inline-flex; flex-direction:column; background:#f7f8fa; border-radius:8px; padding:10px 14px; margin:0 8px 8px 0; min-width:120px; }}
  .stat .label {{ font-size:0.78rem; color:#57606a; }}
  .stat .value {{ font-size:1.05rem; font-weight:600; }}
  .empty {{ color:#8a8f98; font-size:0.9rem; }}
  footer {{ text-align:center; color:#8a8f98; font-size:0.8rem; margin-top:24px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>📋 오늘의 브리핑</h1>
    <div class="date">{today}</div>
  </header>

  <section>
    <h2>🐭 오늘의 운세</h2>
    {fortune}
  </section>

  <section>
    <h2>📰 뉴스</h2>
    {news}
  </section>

  <section>
    <h2>💹 금융지표</h2>
    {market}
  </section>

  <section>
    <h2>🏠 부동산 (전국)</h2>
    {realestate}
  </section>

  <footer>🕐 마지막 업데이트: {updated}</footer>
</div>
</body>
</html>
"""


def build_page():
    fortune = build_fortune()
    news = build_news()
    market = build_market()
    realestate = build_realestate()

    html_out = PAGE_TEMPLATE.format(
        today=esc(TODAY_STR),
        updated=esc(UPDATED_STR),
        fortune=render_fortune(fortune),
        news=render_news(news),
        market=render_market(market),
        realestate=render_realestate(realestate),
    )
    return html_out


def main():
    page = build_page()
    with open("briefing.html", "w", encoding="utf-8") as f:
        f.write(page)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(page)
    print("briefing.html / index.html 생성 완료:", TODAY_STR)


if __name__ == "__main__":
    main()
