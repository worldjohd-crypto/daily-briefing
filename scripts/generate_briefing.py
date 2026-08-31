#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_briefing.py
매일 자동으로 실행되어 briefing.html 을 생성하는 스크립트.

원본 페이지(busanfransisco-source/naver-realestate-bot)의 실제 수집 로직을
그대로 이식했다. 각 섹션은 서로 독립적으로 try/except 로 감싸, 한 섹션이
실패해도 나머지 섹션은 정상적으로 만들어지도록 설계했다.

섹션 구성:
  오늘의 운세 / 날씨 / 오늘의 퀵뉴스 / [정치 논평] / [경제 논평] /
  청약 소식 / 부동산 주간 시세동향 / 기름값·환율 / 금·은·코인 /
  주간 베스트셀러 / 부동산 뉴스 / [부동산 시장 논평] /
  세계 뉴스 / [국제정세 논평] / 금융 뉴스 / [금융시장 논평] /
  AI 뉴스 / [AI·테크 논평]

※ [ ] 표시된 6개 논평 섹션은 원본의 "정책분석" 코너를 그대로 이식한 것이
   아니라, 그날 수집한 뉴스를 바탕으로 Claude API(Anthropic)가 매번 새로
   생성하는 짧은 전문가 논평이다. ANTHROPIC_API_KEY 시크릿이 없으면 해당
   섹션은 안내 문구로 대체되고 나머지 섹션에는 영향이 없다.
"""

import datetime
import html
import json
import os
import re
import sys
import time
import traceback
from collections import Counter
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)

WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
TODAY_STR = f"{NOW.year}년 {NOW.month}월 {NOW.day}일 {WEEKDAY_KO[NOW.weekday()]}"
UPDATED_STR = NOW.strftime("%m월 %d일 %H:%M")
YY2 = NOW.year % 100
DATE_HEAD = f"{YY2}년 {NOW.month}월 {NOW.day}일 {WEEKDAY_KO[NOW.weekday()]}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=kw.pop("timeout", 15), **kw)
    r.raise_for_status()
    return r


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
    """내부 fetch_* 헬퍼용 데코레이터. 실패 시 None을 반환한다."""

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


def sign_fmt(v, decimals=2, suffix=""):
    return f"{'▲' if v > 0 else ('▼' if v < 0 else '-')}{abs(v):,.{decimals}f}{suffix}"


# ---------------------------------------------------------------------------
# 1) 오늘의 운세 — askjiyun.com "오늘의 운세" 게시판에서 오늘 날짜 글을 찾아 추출
# ---------------------------------------------------------------------------
ASKJIYUN_BASE = "https://askjiyun.com/"


def _find_today_document_srl(month, day):
    keyword = f"{month}월 {day}일"
    url = ASKJIYUN_BASE + "?mid=today&search_target=title&search_keyword=" + quote(keyword)
    resp = get(url, timeout=20)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    best_srl = None
    title_needle = f"오늘의 운세, {keyword}"
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if text != title_needle:
            continue
        m = re.search(r"document_srl=(\d+)", a["href"])
        if not m:
            continue
        srl = int(m.group(1))
        if best_srl is None or srl > best_srl:
            best_srl = srl
    return best_srl, title_needle


def _fetch_fortune_document(srl):
    url = f"{ASKJIYUN_BASE}?mid=today&document_srl={srl}"
    resp = get(url, timeout=20)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    full_text = soup.get_text("")
    full_text = re.sub(r"[ \t]+", " ", full_text)
    full_text = re.sub(r"\s+년생", "년생", full_text)
    full_text = re.sub(r"\s+([.%,)])", r"\1", full_text)
    full_text = re.sub(r"([(〈])\s+", r"\1", full_text)
    full_text = re.sub(r"\s+([〉])", r"\1", full_text)

    start_idx = full_text.find("〈")
    end_idx = full_text.find("이 게시물을")

    lunar_line = ""
    if start_idx != -1:
        window = full_text[:start_idx]
        compact = re.sub(r"\s+", "", window)
        m = re.search(r"\[음력(\d+)월(\d+)일\]일진:?([^\(\)\[\]]+?)(?:\(([^)]*)\))?(?=\[|$)", compact)
        if m:
            lm, ld, ganji, hanja = m.groups()
            lunar_line = f"[음력 {lm}월 {ld}일] 일진: {ganji.strip()}" + (f"({hanja})" if hanja else "")

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        body = full_text[start_idx:end_idx]
    else:
        body = full_text

    body = re.sub(r"\.(운세지수)", r". \1", body)
    body = re.sub(r"\s*(〈[^〉]+〉)\s*", r"\n\n\1\n", body)
    body = re.sub(r"(애정\s*\d+)\s*", r"\1\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)

    lines = [ln.strip() for ln in body.splitlines()]
    lines = [ln for ln in lines if ln]
    body = "\n".join(lines)
    body = body.replace("\n〈", "\n\n〈")
    body = re.sub(r"[ \t]*(운세지수 ?\d+%)", r"\n\n\1", body)
    return lunar_line, body


@safe("운세")
def build_fortune():
    header_line1 = f"오늘의 운세, {NOW.month}월 {NOW.day}일"
    srl, _ = _find_today_document_srl(NOW.month, NOW.day)
    if not srl:
        return None
    lunar_line, body = _fetch_fortune_document(srl)
    if not body:
        return None
    header = header_line1 + ("\n" + lunar_line if lunar_line else "")
    return header + "\n\n" + body


# ---------------------------------------------------------------------------
# 2) 날씨 — Open-Meteo (15개 도시, 오전/오후 하늘상태 + 최저·최고기온)
# ---------------------------------------------------------------------------
CITIES = [
    ("서울", 37.5665, 126.9780), ("인천", 37.4563, 126.7052), ("수원", 37.2636, 127.0286),
    ("춘천", 37.8813, 127.7298), ("강릉", 37.7519, 128.8761), ("청주", 36.6424, 127.4890),
    ("대전", 36.3504, 127.3845), ("세종", 36.4801, 127.2891), ("전주", 35.8242, 127.1480),
    ("광주", 35.1595, 126.8526), ("대구", 35.8714, 128.6014), ("부산", 35.1796, 129.0756),
    ("울산", 35.5384, 129.3114), ("창원", 35.2281, 128.6811), ("제주", 33.4996, 126.5312),
]


def _weather_code_to_emoji(code):
    if code == 0:
        return "☀️"
    if code == 1:
        return "🌤️"
    if code == 2:
        return "⛅"
    if code == 3:
        return "☁️"
    if code in (45, 48):
        return "🌫️"
    if code in (51, 53, 55, 56, 57):
        return "🌦️"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "🌧️"
    if code in (71, 73, 75, 77, 85, 86):
        return "❄️"
    if code in (95, 96, 99):
        return "⛈️"
    return "☁️"


def _most_common_code(codes):
    if not codes:
        return 3
    return Counter(codes).most_common(1)[0][0]


@safe("날씨")
def build_weather():
    lats = ",".join(str(lat) for _, lat, _ in CITIES)
    lons = ",".join(str(lon) for _, _, lon in CITIES)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        "&hourly=weather_code&daily=temperature_2m_max,temperature_2m_min"
        "&timezone=Asia%2FSeoul&forecast_days=1"
    )
    resp = get(url, timeout=30)
    results = resp.json()
    if isinstance(results, dict):
        results = [results]

    lines = [TODAY_STR, "", "❒ 지역별 날씨전망 ❒", ""]
    ok = False
    for (name, _, _), data in zip(CITIES, results):
        try:
            hourly_codes = data["hourly"]["weather_code"]
            am_emoji = _weather_code_to_emoji(_most_common_code(hourly_codes[6:12]))
            pm_emoji = _weather_code_to_emoji(_most_common_code(hourly_codes[12:18]))
            tmax = round(data["daily"]["temperature_2m_max"][0])
            tmin = round(data["daily"]["temperature_2m_min"][0])
            lines.append(f"✫{name}({am_emoji})➠({pm_emoji})  {tmin}℃ ~ {tmax}℃")
            ok = True
        except Exception:
            lines.append(f"✫{name}(?)➠(?) 가져오기 실패")
    if not ok:
        return None
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3) 오늘의 퀵뉴스 — Daum 뉴스 섹션별 리드문 + 오늘의 명언 + 주요 경제 지표
# ---------------------------------------------------------------------------
NEWS_SECTIONS = [
    (["politics"], "정치", "🏛️", 3),
    (["economic", "economy"], "경제", "💰", 5),
    (["society"], "사회", "👥", 8),
    (["foreign", "world"], "국제", "🌏", 2),
]
WEATHER_KEYWORDS = re.compile(r"폭염|한파|장마|태풍|폭우|폭설|호우|무더위|열대야|미세먼지|황사|찜통|맹추위|눈|비바람")
ARTICLE_LINK_RE = re.compile(r"https://v\.daum\.net/v/\d+")
JUNK_RE = re.compile(r"무단전재|재배포 금지|저작권|기자 *=|@[\w.]+|▶|☞|【|사진=|영상=|그래픽=")

QUOTES = [
    "행복하게 여행하려면 가볍게 여행해야 한다. -앙투안 드 생텍쥐페리",
    "삶이 있는 한 희망은 있다. -키케로",
    "산다는 것 그것은 치열한 전투이다. -로맹 롤랑",
    "하루라도 책을 읽지 않으면 입안에 가시가 돋는다. -안중근",
    "언제나 현재에 집중할 수 있다면 행복할 것이다. -파울로 코엘료",
    "진정으로 웃으려면 고통을 참아야 하며, 나아가 고통을 즐길 줄 알아야 한다. -찰리 채플린",
    "직업에서 행복을 찾아라. 아니면 행복이 무엇인지 절대 모를 것이다. -엘버트 허버드",
    "피할 수 없으면 즐겨라. -로버트 엘리엇",
    "절대 어제를 후회하지 마라. 인생은 오늘의 나 안에 있고 내일은 스스로 만드는 것이다. -론 허바드",
    "계단을 밟아야 계단 위에 올라설 수 있다. -터키 속담",
    "오랫동안 꿈을 그리는 사람은 마침내 그 꿈을 닮아간다. -앙드레 말로",
    "좋은 성과를 얻으려면 한 걸음 한 걸음이 힘차고 충실하지 않으면 안 된다. -단테",
    "행복은 습관이다. 그것을 몸에 지니라. -허버드",
    "성공의 비결은 단 한 가지, 잘할 수 있는 일에 광적으로 집중하는 것이다. -톰 모나건",
    "자신감 있는 표정을 지으면 자신감이 생긴다. -찰스 다윈",
    "평생 살 것처럼 꿈을 꾸어라. 그리고 내일 죽을 것처럼 오늘을 살아라. -제임스 딘",
    "네 믿음은 네 생각이 된다. 네 생각은 네 말이 된다. 네 말은 네 행동이 된다. -간디",
    "일하는 시간과 노는 시간을 뚜렷이 구분하라. -루이사 메이 올콧",
    "행동은 모든 성공의 가장 기초적인 열쇠이다. -파블로 피카소",
    "성공하려면 이미 했던 일을 제대로 활용하라. -블레이크 로스",
    "1퍼센트의 가능성, 그것이 나의 길이다. -나폴레옹",
    "그대 자신의 영혼을 탐구하라. 다른 누구에게도 의지하지 말고 오직 그대 혼자의 힘으로 하라. -윤동주",
    "고통이 남기고 간 뒤를 보라. 고난이 지나면 반드시 기쁨이 스며든다. -괴테",
    "사막이 아름다운 것은 어딘가에 샘이 숨겨져 있기 때문이다. -생텍쥐페리",
    "꿈을 계속 간직하고 있으면 반드시 실현할 때가 온다. -괴테",
    "화려한 일을 추구하지 말라. 중요한 것은 스스로의 재능이며 자신의 행동에 쏟아붓는 사랑의 정도이다. -마더 테레사",
    "마음만을 가지고 있어서는 안 된다. 반드시 실천하여야 한다. -이소룡",
    "겨울이 오면 봄이 멀지 않으리. -셸리",
    "일이 즐거우면 인생은 낙원이다. 일이 의무이면 인생은 지옥이다. -막심 고리키",
    "먼저 자신을 비웃어라. 다른 사람이 당신을 비웃기 전에. -엘사 맥스웰",
    "시작이 반이다. -아리스토텔레스",
    "인생은 자전거를 타는 것과 같다. 균형을 잡으려면 움직여야 한다. -아인슈타인",
    "가장 큰 위험은 위험 없는 삶이다. -스티븐 코비",
    "내일은 내일의 태양이 뜬다. -마거릿 미첼",
    "최고에 도달하려면 최저에서 시작하라. -푸블릴리우스 시루스",
    "천 리 길도 한 걸음부터. -노자",
    "배움은 우연히 얻어지는 것이 아니라 열성을 다해 갈구하고 부지런히 집중해야 얻을 수 있는 것이다. -애비게일 애덤스",
    "성공은 넘어지는 횟수보다 한 번 더 일어나는 것이다. -올리버 골드스미스",
    "습관은 제2의 천성이다. -키케로",
    "오늘 할 수 있는 일에 전력을 다하라. 그러면 내일에는 한 걸음 더 진보한다. -뉴턴",
    "행복의 문이 하나 닫히면 다른 문이 열린다. -헬렌 켈러",
    "아는 것만으로는 충분하지 않다. 적용해야 한다. 의지만으로는 충분하지 않다. 실행해야 한다. -괴테",
    "기회는 준비된 자에게 온다. -파스퇴르",
    "느리더라도 꾸준한 자가 경주에서 이긴다. -이솝",
    "어리석은 자는 멀리서 행복을 찾고, 현명한 자는 자신의 발치에서 행복을 키워간다. -제임스 오펜하임",
    "절망하지 마라. 종종 열쇠 꾸러미의 마지막 열쇠가 자물쇠를 연다. -탈무드",
    "당신이 할 수 있다고 믿든 할 수 없다고 믿든, 믿는 대로 될 것이다. -헨리 포드",
    "잔잔한 바다에서는 좋은 뱃사공이 만들어지지 않는다. -영국 속담",
    "독서는 완성된 사람을 만들고, 토론은 준비된 사람을, 글쓰기는 정확한 사람을 만든다. -베이컨",
    "넘어진 것은 부끄러운 일이 아니다. 일어나지 않는 것이 부끄러운 일이다. -유대 격언",
    "세월은 사람을 기다려주지 않는다. -도연명",
    "작은 기회로부터 종종 위대한 업적이 시작된다. -데모스테네스",
    "인내는 쓰지만 그 열매는 달다. -루소",
    "정직만큼 부유한 유산은 없다. -셰익스피어",
    "말보다 행동이 더 큰 소리를 낸다. -에머슨",
    "건강한 신체에 건강한 정신이 깃든다. -유베날리스",
    "시간을 지배하는 사람이 인생을 지배한다. -에센 바흐",
    "남을 아는 사람은 지혜롭고, 자신을 아는 사람은 명철하다. -노자",
    "낭비한 시간에 대한 후회는 더 큰 시간 낭비이다. -메이슨 쿨리",
    "비관론자는 모든 기회에서 어려움을 보고, 낙관론자는 모든 어려움에서 기회를 본다. -처칠",
]


def _daum_section_articles(slugs, want):
    for slug in slugs:
        try:
            resp = get(f"https://news.daum.net/{slug}", timeout=12)
        except Exception:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        items, seen = [], set()
        for a in soup.find_all("a", href=ARTICLE_LINK_RE):
            href = ARTICLE_LINK_RE.search(a.get("href", "")).group(0)
            title = a.get_text(" ", strip=True)
            if href in seen or not title or len(title) < 10:
                continue
            seen.add(href)
            items.append((title, href))
            if len(items) >= want * 3:
                break
        if items:
            return items
    return []


def _daum_article_summary(url, max_sentences=2, max_chars=280):
    resp = get(url, timeout=12)
    soup = BeautifulSoup(resp.text, "html.parser")
    root = soup.select_one("div.article_view") or soup
    paras = []
    for p in root.find_all("p"):
        t = p.get_text(" ", strip=True)
        if not t or len(t) < 25 or JUNK_RE.search(t):
            continue
        paras.append(t)
    text = re.sub(r"\s+", " ", " ".join(paras[:4])).strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    picked = []
    for s in sentences:
        s = s.strip()
        s = re.sub(r"^\[[^\]]{2,40}\]\s*", "", s)
        s = re.sub(r"^【[^】]{2,40}】\s*", "", s)
        s = re.sub(r"^[가-힣A-Za-z]+=\S*\s*기자\s*", "", s)
        if len(s) < 10:
            continue
        if not picked and re.match(r"^(반면|하지만|그러나|한편|또한|이에 따라|이어)\b", s):
            continue
        picked.append(s)
        if len(picked) >= max_sentences:
            break
    summary = " ".join(picked).strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0] + "…"
    return summary


def _collect_daum_news():
    items = []
    used = set()

    top_candidates = []
    try:
        resp = get("https://news.daum.net", timeout=12)
        soup = BeautifulSoup(resp.text, "html.parser")
        seen = set()
        for a in soup.find_all("a", href=ARTICLE_LINK_RE):
            href = ARTICLE_LINK_RE.search(a.get("href", "")).group(0)
            title = a.get_text(" ", strip=True)
            if href in seen or not title or len(title) < 10:
                continue
            seen.add(href)
            top_candidates.append((title, href))
            if len(top_candidates) >= 5:
                break
    except Exception:
        pass

    for title, href in top_candidates[:1]:
        try:
            summary = _daum_article_summary(href) or title
            items.append(("🔥", "톱뉴스", summary))
            used.add(href)
        except Exception:
            pass

    weather_item = None
    for slugs, label, emoji, want in NEWS_SECTIONS:
        candidates = _daum_section_articles(slugs, want)
        count = 0
        for title, href in candidates:
            if href in used:
                continue
            if weather_item is None and WEATHER_KEYWORDS.search(title):
                try:
                    summary = _daum_article_summary(href) or title
                    weather_item = ("☀️", "날씨", summary)
                    used.add(href)
                    continue
                except Exception:
                    pass
            if count >= want:
                continue
            try:
                summary = _daum_article_summary(href)
            except Exception:
                summary = ""
            if not summary:
                continue
            items.append((emoji, label, summary))
            used.add(href)
            count += 1
            time.sleep(0.2)

    if weather_item:
        items.append(weather_item)
    return items


def _todays_quote():
    if not QUOTES:
        return None
    return QUOTES[NOW.timetuple().tm_yday % len(QUOTES)]


def _fmt_num(v, decimals=2):
    return f"{v:,.{decimals}f}" if decimals else f"{int(round(v)):,}"


def _get_market_indicators():
    rows = []

    def naver_index(code):
        resp = get(f"https://finance.naver.com/sise/sise_index.naver?code={code}")
        soup = BeautifulSoup(resp.text, "html.parser")
        el = soup.select_one("#now_value")
        return float(el.get_text(strip=True).replace(",", ""))

    for code, name in [("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("KPI100", "코스피100")]:
        try:
            rows.append((name, _fmt_num(naver_index(code))))
        except Exception:
            pass

    try:
        resp = get("https://finance.naver.com/marketindex/")
        soup = BeautifulSoup(resp.text, "html.parser")
        el = soup.select_one("div.head_info > span.value")
        rows.append(("달러", _fmt_num(float(el.get_text(strip=True).replace(",", "")))))
    except Exception:
        pass

    for sym, name in [("^IXIC", "나스닥"), ("^DJI", "다우지수"), ("^GSPC", "S&P500"), ("GC=F", "GOLD(금)")]:
        try:
            resp = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range=1d&interval=1d")
            price = resp.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
            rows.append((name, _fmt_num(float(price))))
        except Exception:
            pass

    try:
        resp = get("https://api.upbit.com/v1/ticker?markets=KRW-BTC")
        price = resp.json()[0]["trade_price"]
        rows.append(("비트코인", _fmt_num(price, 0)))
    except Exception:
        pass

    return rows


_DAUM_NEWS_CACHE = None


def _daum_news_cached():
    """_collect_daum_news() 는 매번 다음 여러 섹션을 스크래핑하므로,
    퀵뉴스 섹션과 정치/경제 논평 섹션이 같은 결과를 재사용하도록 캐싱한다."""
    global _DAUM_NEWS_CACHE
    if _DAUM_NEWS_CACHE is None:
        try:
            _DAUM_NEWS_CACHE = _collect_daum_news()
        except Exception:
            _DAUM_NEWS_CACHE = []
    return _DAUM_NEWS_CACHE


@safe("퀵뉴스")
def build_shortnews():
    header = f"{DATE_HEAD} 오늘의 퀵뉴스⚡"
    news_items = _daum_news_cached()

    lines = [header, ""]
    if news_items:
        for emoji, label, summary in news_items:
            lines.append(f"{emoji} ({label}) {summary}")
        lines.append("")
    else:
        lines.append("(오늘 뉴스 수집에 실패했습니다)")
        lines.append("")

    quote_txt = _todays_quote()
    if quote_txt:
        lines.append("[오늘의 명언]")
        lines.append(quote_txt)
        lines.append("")

    indicators = _get_market_indicators()
    if indicators:
        lines.append("[주요 경제 지표]")
        for name, val in indicators:
            lines.append(f"  - {name} : {val}")

    if not news_items and not indicators:
        return None
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# AI 논평 공통 헬퍼 — Claude API(Anthropic)로 그날 수집한 뉴스를 바탕으로
# 짧은 전문가 페르소나 논평을 생성한다. ANTHROPIC_API_KEY 가 없거나 호출에
# 실패해도 해당 섹션만 안내 문구로 대체되고 나머지 섹션에는 영향 없다.
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL_OVERRIDE = os.environ.get("ANTHROPIC_MODEL", "")
# 모델 별칭은 시간이 지나면 폐지될 수 있으므로(404 Not Found), 후보를 여러 개
# 두고 앞에서부터 시도한다. 마지막 후보(claude-sonnet-5)는 이 저장소를 만든
# 시점에 실제로 서비스 중인 것이 확인된 모델이라 항상 동작해야 한다.
# ANTHROPIC_MODEL 시크릿/변수를 지정하면 그 값만 사용한다.
ANTHROPIC_MODEL_CANDIDATES = (
    [ANTHROPIC_MODEL_OVERRIDE]
    if ANTHROPIC_MODEL_OVERRIDE
    else ["claude-haiku-4-5", "claude-sonnet-5"]
)
COMMENT_FALLBACK = (
    "ANTHROPIC_API_KEY가 설정되지 않았거나 생성에 실패해 논평을 표시할 수 없습니다."
)


def _ask_claude(persona_prompt, material_lines, max_tokens=400):
    if not ANTHROPIC_API_KEY or not material_lines:
        return None
    material = "\n".join(f"- {m}" for m in material_lines[:8])
    messages = [
        {
            "role": "user",
            "content": (
                f"오늘({TODAY_STR}) 수집된 관련 뉴스 헤드라인/요약은 다음과 같습니다:\n\n"
                f"{material}\n\n"
                "위 내용을 참고해서 페르소나에 맞는 논평을 작성해줘."
            ),
        }
    ]
    resp = None
    for i, model in enumerate(ANTHROPIC_MODEL_CANDIDATES):
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0.7,
                "system": persona_prompt,
                "messages": messages,
            },
            timeout=30,
        )
        if resp.status_code == 404 and i < len(ANTHROPIC_MODEL_CANDIDATES) - 1:
            # 이 모델 별칭을 찾을 수 없음 -> 다음 후보 모델로 재시도
            continue
        break
    resp.raise_for_status()
    data = resp.json()
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    text = "\n".join(parts).strip()
    return text or None


def _build_commentary(section_label, persona_name, persona_prompt, material_lines):
    text = _ask_claude(persona_prompt, material_lines)
    if not text:
        return None
    header = f"{DATE_HEAD} {section_label} ({persona_name})"
    return f"{header}\n\n{text}"


@safe("정치 논평", fallback=COMMENT_FALLBACK)
def build_politics_comment():
    material = [s for e, l, s in _daum_news_cached() if l == "정치"]
    return _build_commentary(
        "정치 논평",
        "정치 브리핑",
        "너는 냉철하고 균형 잡힌 시각을 가진 한국 정치 평론가야. 특정 정당에 "
        "편향되지 않고, 오늘자 정치 뉴스의 핵심 쟁점과 그 파장을 3~4문장, "
        "300자 내외의 한국어로 간결하게 짚어줘. 자극적인 단정은 피하고 "
        "사실관계 중심으로 서술해.",
        material,
    )


@safe("경제 논평", fallback=COMMENT_FALLBACK)
def build_economy_comment():
    material = [s for e, l, s in _daum_news_cached() if l == "경제"]
    return _build_commentary(
        "경제 논평",
        "경제 브리핑",
        "너는 거시경제와 산업 동향에 밝은 경제 전문 애널리스트야. 오늘자 경제 "
        "뉴스를 바탕으로 시장에 어떤 의미가 있는지 3~4문장, 300자 내외의 "
        "한국어로 간결하고 담백하게 설명해줘. 투자 조언이 아니라 상황 해설에 "
        "집중해.",
        material,
    )


# ---------------------------------------------------------------------------
# 4) 청약 소식 + 5) 부동산 주간 시세동향 — 청약홈 / 한국부동산원 R-ONE
# ---------------------------------------------------------------------------
APPLYHOME_URL = "https://www.applyhome.co.kr/ai/aia/selectAPTLttotPblancListView.do"
DATE_RANGE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
REGION_RE = re.compile(r"^[가-힣]{2}$")

RONE_URL = "https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do"
RONE_KEY = os.environ.get("RONE_KEY", "")
STATBL_SALE = "T244183132827305"
STATBL_JEONSE = "T247713133046872"
REGION_ORDER = ["전국", "수도권", "지방권", "서울", "경기", "인천", "부산", "대구", "광주",
                 "대전", "울산", "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
REGION_LABEL = {"지방권": "지방"}


def _parse_d(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d").date()


@safe_none("청약")
def fetch_subscriptions():
    today = NOW.date()
    begin_month = (today.replace(day=1) - datetime.timedelta(days=60)).strftime("%Y%m")
    end_month = (today.replace(day=1) + datetime.timedelta(days=32)).strftime("%Y%m")
    rows = []
    page = 1
    while page <= 15:
        try:
            resp = requests.post(
                APPLYHOME_URL, headers=HEADERS,
                data={"beginPd": begin_month, "endPd": end_month, "pageIndex": str(page)},
                timeout=15,
            )
            resp.raise_for_status()
        except Exception:
            if page == 1:
                raise
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        page_rows = []
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) < 9 or not REGION_RE.match(cells[0]):
                    continue
                page_rows.append(cells)
        if not page_rows:
            break
        for cells in page_rows:
            m = DATE_RANGE_RE.search(cells[7])
            if not m:
                continue
            start, end = _parse_d(m.group(1)), _parse_d(m.group(2))
            if end < today or start > today + datetime.timedelta(days=14):
                continue
            am = DATE_RE.search(cells[8])
            kind_raw = cells[1]
            kind = "공공" if "국민" in kind_raw else ("민간" if "민영" in kind_raw else kind_raw)
            rows.append({"region": cells[0], "name": cells[3], "kind": kind,
                          "start": start, "end": end,
                          "announce": _parse_d(am.group(0)) if am else None})
        page += 1
        time.sleep(0.3)
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda r: (r["start"], r["region"])):
        key = (r["region"], r["name"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq[:20]


@safe("청약 소식")
def build_subs():
    subs = fetch_subscriptions()
    lines = [f"{DATE_HEAD} 청약 소식", "", "🏗️ 청약 접수 단지 (전국, 2주 이내)", ""]
    today = NOW.date()
    if subs:
        for r in subs:
            status = " ◀ 접수중" if r["start"] <= today <= r["end"] else ""
            announce = f" | 발표 {r['announce'].month}/{r['announce'].day}" if r["announce"] else ""
            lines.append(f"[{r['region']}·{r['kind']}] {r['name']} | 접수 {r['start'].month}/{r['start'].day}~{r['end'].month}/{r['end'].day}{announce}{status}")
        return "\n".join(lines).strip()
    if subs is None:
        return None
    lines.append("(2주 이내 접수 예정인 단지가 없습니다)")
    return "\n".join(lines).strip()


def _rone_call(params, tries=4):
    for i in range(tries):
        try:
            r = requests.get(RONE_URL, params=params, headers=HEADERS, timeout=25)
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def _rone_weekly_changes(statbl):
    js = _rone_call({"KEY": RONE_KEY, "Type": "json", "pIndex": 1, "pSize": 1, "STATBL_ID": statbl, "DTACYCLE_CD": "WK"})
    total = js["SttsApiTblData"][0]["head"][0]["list_total_count"]
    psize = 250
    last_page = (total - 1) // psize + 1
    rows = []
    for p in range(max(1, last_page - 2), last_page + 1):
        time.sleep(1)
        js = _rone_call({"KEY": RONE_KEY, "Type": "json", "pIndex": p, "pSize": psize, "STATBL_ID": statbl, "DTACYCLE_CD": "WK"})
        rows.extend(js["SttsApiTblData"][1]["row"])
    times = sorted({str(x["WRTTIME_IDTFR_ID"]) for x in rows})
    latest, prev = times[-1], times[-2]

    def top(t):
        return {x["CLS_NM"]: x["DTA_VAL"] for x in rows
                if str(x["WRTTIME_IDTFR_ID"]) == t and ">" not in str(x.get("CLS_FULLNM") or "")}

    cur, before = top(latest), top(prev)
    date_desc = next((str(x["WRTTIME_DESC"]) for x in rows if str(x["WRTTIME_IDTFR_ID"]) == latest), "")
    changes = {}
    for name in REGION_ORDER:
        if name in cur and name in before and before[name]:
            changes[name] = (cur[name] / before[name] - 1) * 100
    return date_desc, changes


def _format_region_changes(changes):
    lines, buf = [], []
    for name in REGION_ORDER:
        if name not in changes:
            continue
        label = REGION_LABEL.get(name, name)
        buf.append(f"{label} {changes[name]:+.2f}%")
        if len(buf) == 3:
            lines.append(" | ".join(buf))
            buf = []
    if buf:
        lines.append(" | ".join(buf))
    return lines


RONE_CACHE_FILE = "rone-cache.json"


@safe("부동산 주간 시세동향")
def build_trend():
    if not RONE_KEY:
        return ("한국부동산원 R-ONE API 키(RONE_KEY)가 설정되지 않아 표시할 수 없습니다.\n"
                "https://www.reb.or.kr/r-one 에서 무료로 발급받아 저장소 Settings > "
                "Secrets and variables > Actions 에 등록하면 자동으로 채워집니다.")

    try:
        with open(RONE_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}

    def get_with_cache(statbl, key):
        try:
            d, ch = _rone_weekly_changes(statbl)
            cache[key] = {"date": d, "changes": ch}
            return d, ch
        except Exception:
            c = cache.get(key)
            if c and c.get("changes"):
                return c["date"], c["changes"]
            return None

    sale = get_with_cache(STATBL_SALE, "sale")
    time.sleep(1)
    jeonse = get_with_cache(STATBL_JEONSE, "jeonse")
    try:
        with open(RONE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass

    if not sale and not jeonse:
        return None

    lines = [f"{DATE_HEAD} 부동산 주간 시세동향", ""]
    if sale:
        d, ch = sale
        base = f" ({int(d[5:7])}/{int(d[8:10])} 기준)" if len(d) >= 10 else ""
        lines.append(f"📈 주간 아파트 매매가격 변동률{base}")
        lines.append("")
        lines.extend(_format_region_changes(ch))
        lines.append("")
    if jeonse:
        _, ch = jeonse
        lines.append("🔑 주간 아파트 전세가격 변동률")
        lines.append("")
        lines.extend(_format_region_changes(ch))
        lines.append("")
    lines.append("* 한국부동산원 주간 아파트가격 동향 (전주 대비)")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# 6) 기름값·환율 / 7) 금·은·코인 / 8) 주간 베스트셀러
# ---------------------------------------------------------------------------
FX_LIST = [
    ("USD", "미국 달러"), ("JPY", "일본 엔(100)"), ("EUR", "유럽 유로"),
    ("CNY", "중국 위안"), ("GBP", "영국 파운드"), ("AUD", "호주 달러"),
    ("CAD", "캐나다 달러"), ("CHF", "스위스 프랑"), ("HKD", "홍콩 달러"),
    ("VND", "베트남 동(100)"),
]

COIN_KR = {"BTC": "비트코인", "ETH": "이더리움", "XRP": "리플", "BNB": "비앤비",
           "SOL": "솔라나", "DOGE": "도지코인", "ADA": "에이다", "TRX": "트론",
           "LINK": "체인링크", "AVAX": "아발란체", "XLM": "스텔라루멘", "SUI": "수이",
           "HYPE": "하이퍼리퀴드", "DOT": "폴카닷", "LTC": "라이트코인",
           "SHIB": "시바이누", "TON": "톤코인", "BCH": "비트코인캐시", "HBAR": "헤데라"}
STABLES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "BUSD", "PYUSD", "USDS"}

FX_CACHE_FILE = "fx-cache.json"


@safe_none("네이버 시장지표")
def fetch_naver_market_items():
    r = get("https://finance.naver.com/marketindex/")
    soup = BeautifulSoup(r.text, "html.parser")
    items = {}
    for li in soup.find_all("li"):
        t = li.get_text(" ", strip=True)
        m = re.match(r"^(휘발유|고급휘발유|경유|국제 금|국내 금)\s+([\d,]+\.?\d*)\s*(?:원|달러)\s+([\d,]+\.?\d*)\s+(상승|하락|보합)", t)
        if m and m.group(1) not in items:
            val = float(m.group(2).replace(",", ""))
            chg = float(m.group(3).replace(",", ""))
            if m.group(4) == "하락":
                chg = -chg
            elif m.group(4) == "보합":
                chg = 0.0
            items[m.group(1)] = (val, chg)
    return items


@safe_none("경유값")
def fetch_oil_detail(code):
    r = get(f"https://finance.naver.com/marketindex/oilDetail.naver?marketindexCd={code}")
    soup = BeautifulSoup(r.text, "html.parser")
    today = soup.select_one("p.no_today")
    ex = soup.select_one("p.no_exday")
    val = float(re.sub(r"[^\d.]", "", today.get_text()))
    chg = None
    if ex:
        t = ex.get_text(" ", strip=True)
        m = re.search(r"([\d,]+\.?\d*)", t)
        if m:
            chg = float(m.group(1).replace(",", ""))
            if "하락" in t:
                chg = -chg
    return val, chg


@safe_none("환율")
def fetch_fx_rates():
    r = get("https://finance.naver.com/marketindex/exchangeList.naver")
    soup = BeautifulSoup(r.text, "html.parser")
    rates = {}
    for tr in soup.select("table tbody tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 2:
            continue
        name = cells[0]
        for code, _ in FX_LIST:
            if code in name and code not in rates:
                try:
                    rates[code] = float(cells[1].replace(",", ""))
                except ValueError:
                    pass
    return rates


@safe("기름값·환율")
def build_fuelfx():
    items = fetch_naver_market_items() or {}
    lines = [f"{DATE_HEAD} 기름값·환율", "", "⛽ 전국 평균 기름값 (오피넷 기준, 전일 대비)", ""]
    fuel_rows = []
    if "휘발유" in items:
        v, c = items["휘발유"]
        fuel_rows.append(f"휘발유 {v:,.2f}원/L ({sign_fmt(c)})")
    diesel = fetch_oil_detail("OIL_LO")
    if diesel:
        v, c = diesel
        chg_s = f" ({sign_fmt(c)})" if c is not None else ""
        fuel_rows.append(f"경유 {v:,.2f}원/L{chg_s}")
    lines.extend(fuel_rows or ["(기름값을 가져오지 못했습니다)"])
    lines.append("")
    lines.append("💱 주요국 환율 (매매기준율, 전일 대비)")
    lines.append("")

    rates = fetch_fx_rates() or {}
    try:
        with open(FX_CACHE_FILE, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        prev = {}
    if rates:
        for code, label in FX_LIST:
            if code not in rates:
                continue
            cur = rates[code]
            pct = ""
            if prev.get(code):
                p = (cur / prev[code] - 1) * 100
                pct = f" ({p:+.2f}%)"
            lines.append(f"{label} : {cur:,.2f}원{pct}")
        try:
            with open(FX_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(rates, f)
        except Exception:
            pass
    else:
        lines.append("(환율을 가져오지 못했습니다)")

    if not fuel_rows and not rates:
        return None
    return "\n".join(lines).strip()


@safe_none("국제은시세")
def fetch_silver():
    r = get("https://query1.finance.yahoo.com/v8/finance/chart/SI%3DF?range=1d&interval=1d")
    return r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]


@safe_none("코인시세")
def fetch_coins_top10():
    r = get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={"vs_currency": "krw", "order": "market_cap_desc", "per_page": 20,
                "page": 1, "price_change_percentage": "24h"},
    )
    out = []
    for c in r.json():
        sym = c["symbol"].upper()
        name = c.get("name", "")
        if sym in STABLES or any(w in name for w in ("Staked", "Wrapped", "Bridged", "Heloc")):
            continue
        rank = c.get("market_cap_rank")
        if rank is None or rank > 30:
            continue
        out.append((COIN_KR.get(sym, sym), c["current_price"], c.get("price_change_percentage_24h")))
        if len(out) >= 10:
            break
    return out


@safe("금·은·코인")
def build_metalcoin():
    items = fetch_naver_market_items() or {}
    lines = [f"{DATE_HEAD} 금·은·코인", "", "🥇 금·은 시세 (전일 대비)", ""]
    metal_rows = []
    if "국제 금" in items:
        v, c = items["국제 금"]
        metal_rows.append(f"국제 금 {v:,.2f}달러/온스 ({sign_fmt(c)})")
    if "국내 금" in items:
        v, c = items["국내 금"]
        metal_rows.append(f"국내 금 {v:,.0f}원/g ({sign_fmt(c, 0)})")
    silver = fetch_silver()
    if silver:
        metal_rows.append(f"국제 은 {silver:,.2f}달러/온스")
    lines.extend(metal_rows or ["(금·은 시세를 가져오지 못했습니다)"])
    lines.append("")
    lines.append("🪙 코인 시가총액 상위 10 (24시간 등락)")
    lines.append("")

    coins = fetch_coins_top10()
    if coins:
        for name, price, pct in coins:
            price_s = f"{price:,.0f}원" if price >= 100 else f"{price:,.2f}원"
            pct_s = f" ({pct:+.2f}%)" if pct is not None else ""
            lines.append(f"{name} {price_s}{pct_s}")
    else:
        lines.append("(코인 시세를 가져오지 못했습니다)")

    if not metal_rows and not coins:
        return None
    return "\n".join(lines).strip()


ALADIN_TTB_KEY = os.environ.get("ALADIN_TTB_KEY", "")


@safe_none("베스트셀러(스크래핑)")
def _fetch_bestsellers_scrape():
    r = get("https://www.aladin.co.kr/shop/common/wbest.aspx?BestType=Bestseller&BranchType=1&CID=0")
    soup = BeautifulSoup(r.text, "html.parser")
    titles = []
    for a in soup.select("a.bo3"):
        t = a.get_text(" ", strip=True)
        if t and t not in titles:
            titles.append(t)
        if len(titles) >= 10:
            break
    return titles


@safe_none("베스트셀러(TTB API)")
def _fetch_bestsellers_api():
    if not ALADIN_TTB_KEY:
        return None
    r = get(
        "https://www.aladin.co.kr/ttb/api/ItemList.aspx",
        params={
            "ttbkey": ALADIN_TTB_KEY, "QueryType": "Bestseller", "MaxResults": 10,
            "start": 1, "SearchTarget": "Book", "output": "js", "Version": "20131101",
        },
    )
    items = r.json().get("item", [])
    return [it["title"] for it in items if it.get("title")]


def fetch_bestsellers():
    # 알라딘 웹페이지 직접 스크래핑을 우선 시도하고 (API 키 불필요),
    # 봇 차단 등으로 실패하면 TTB API 키(ALADIN_TTB_KEY, 선택)가 있을 때 그것으로 대체한다.
    titles = _fetch_bestsellers_scrape()
    if titles:
        return titles
    return _fetch_bestsellers_api()


@safe("주간 베스트셀러")
def build_books():
    books = fetch_bestsellers()
    if not books:
        return None
    lines = [f"{DATE_HEAD} 주간 베스트셀러", "", "📚 알라딘 주간 베스트셀러 TOP 10", ""]
    for i, t in enumerate(books, 1):
        lines.append(f"{i}. {t}")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# 9~12) 부동산 / 세계 / 금융 / AI 뉴스 — 네이버뉴스 섹션 · AI타임스 스크래핑
# ---------------------------------------------------------------------------
def _naver_section_headlines(urls, max_articles=10):
    if isinstance(urls, str):
        urls = [urls]
    for url in urls:
        try:
            resp = get(url)
        except Exception:
            continue
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = soup.select("a.sa_text_title, a.sa_thumb_link, a.cluster_text_headline")
        if not candidates:
            candidates = soup.find_all("a", href=re.compile(r"/article/"))
        articles = []
        seen_links = set()
        for a_tag in candidates:
            href = a_tag.get("href", "").strip()
            if not href or href in seen_links:
                continue
            title = a_tag.get_text(strip=True)
            if not title:
                strong = a_tag.find("strong")
                if strong:
                    title = strong.get_text(strip=True)
            if not title or "동영상" in title:
                continue
            if href.startswith("/"):
                href = "https://news.naver.com" + href
            articles.append({"title": title, "url": href})
            seen_links.add(href)
            if len(articles) >= max_articles:
                break
        if articles:
            return articles
    return []


def _render_news_section(emoji, label, articles):
    lines = [f"{emoji} {DATE_HEAD} {label}", ""]
    for art in articles:
        lines.append(art["title"])
        lines.append(art["url"])
        lines.append("")
    return "\n".join(lines).strip()


_SECTION_ARTICLES_CACHE = {}


def _cached_section_headlines(cache_key, urls, max_articles=10):
    """뉴스 섹션과 그에 대응하는 논평 섹션이 같은 스크래핑 결과를
    재사용하도록 캐싱한다 (사이트에 중복 요청을 보내지 않기 위함)."""
    if cache_key not in _SECTION_ARTICLES_CACHE:
        _SECTION_ARTICLES_CACHE[cache_key] = _naver_section_headlines(
            urls, max_articles=max_articles
        )
    return _SECTION_ARTICLES_CACHE[cache_key]


@safe("부동산 뉴스")
def build_realestate_news():
    articles = _cached_section_headlines(
        "realestate", "https://news.naver.com/breakingnews/section/101/260"
    )
    if not articles:
        return None
    return _render_news_section("🏠", "부동산 주요뉴스", articles)


@safe("부동산 시장 논평", fallback=COMMENT_FALLBACK)
def build_realestate_comment():
    articles = _cached_section_headlines(
        "realestate", "https://news.naver.com/breakingnews/section/101/260"
    )
    material = [a["title"] for a in (articles or [])]
    return _build_commentary(
        "부동산 시장 논평",
        "부동산 워치",
        "너는 오랜 경력의 부동산 시장 분석가야. 오늘자 부동산 관련 뉴스 "
        "제목들을 보고 정책, 공급, 가격 동향 중 어떤 흐름이 눈에 띄는지 "
        "3~4문장, 300자 내외의 한국어로 담백하게 짚어줘. 특정 지역의 매수·"
        "매도를 직접 권유하지는 마.",
        material,
    )


@safe("세계 뉴스")
def build_world_news():
    articles = _cached_section_headlines(
        "world",
        [
            "https://news.naver.com/section/104",
            "https://news.naver.com/breakingnews/section/104",
        ],
    )
    if not articles:
        return None
    return _render_news_section("🌏", "세계 뉴스", articles)


@safe("국제정세 논평", fallback=COMMENT_FALLBACK)
def build_world_comment():
    articles = _cached_section_headlines(
        "world",
        [
            "https://news.naver.com/section/104",
            "https://news.naver.com/breakingnews/section/104",
        ],
    )
    material = [a["title"] for a in (articles or [])]
    return _build_commentary(
        "국제정세 논평",
        "글로벌 브리핑",
        "너는 국제정세를 다루는 외신 데스크 기자야. 오늘자 세계 뉴스 "
        "제목들을 보고 한국에 미칠 수 있는 영향까지 포함해 3~4문장, 300자 "
        "내외의 한국어로 균형 잡히게 정리해줘.",
        material,
    )


@safe("금융 뉴스")
def build_finance_news():
    articles = _cached_section_headlines(
        "finance", "https://news.naver.com/breakingnews/section/101/259"
    )
    if not articles:
        return None
    return _render_news_section("🏦", "금융 뉴스", articles)


@safe("금융시장 논평", fallback=COMMENT_FALLBACK)
def build_finance_comment():
    articles = _cached_section_headlines(
        "finance", "https://news.naver.com/breakingnews/section/101/259"
    )
    material = [a["title"] for a in (articles or [])]
    return _build_commentary(
        "금융시장 논평",
        "마켓 브리핑",
        "너는 국내 금융시장을 다루는 애널리스트야. 오늘자 금융 뉴스 제목들을 "
        "보고 은행·증시·코인 등 시장 전반의 분위기를 3~4문장, 300자 내외의 "
        "한국어로 간결하게 정리해줘. 특정 종목 매수·매도 추천은 하지 마.",
        material,
    )


AI_ARTICLE_RE = re.compile(r"/news/articleView\.html\?idxno=\d+")
_AI_ARTICLES_CACHE = None


def _fetch_ai_articles():
    global _AI_ARTICLES_CACHE
    if _AI_ARTICLES_CACHE is not None:
        return _AI_ARTICLES_CACHE
    try:
        resp = get("https://www.aitimes.com/news/articleList.html?box_idxno=10&view_type=sm")
    except Exception:
        _AI_ARTICLES_CACHE = []
        return _AI_ARTICLES_CACHE
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    seen_links = set()
    for a_tag in soup.find_all("a", href=AI_ARTICLE_RE):
        href = a_tag.get("href", "").strip()
        if href.startswith("/"):
            href = "https://www.aitimes.com" + href
        if href in seen_links:
            continue
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        articles.append({"title": title, "url": href})
        seen_links.add(href)
        if len(articles) >= 10:
            break
    _AI_ARTICLES_CACHE = articles
    return articles


@safe("AI 뉴스")
def build_ai_news():
    articles = _fetch_ai_articles()
    if not articles:
        return None
    return _render_news_section("🤖", "AI 뉴스", articles)


@safe("AI·테크 논평", fallback=COMMENT_FALLBACK)
def build_ai_comment():
    articles = _fetch_ai_articles()
    material = [a["title"] for a in (articles or [])]
    return _build_commentary(
        "AI·테크 논평",
        "테크 트렌드 워치",
        "너는 AI와 테크 산업을 취재하는 전문 기자야. 오늘자 AI 뉴스 제목들을 "
        "보고 어떤 흐름이 중요한지 3~4문장, 300자 내외의 한국어로 담백하게 "
        "짚어줘.",
        material,
    )


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
        ("fortune", "🔮", "오늘의 운세", build_fortune()),
        ("weather", "☀️", "날씨", build_weather()),
        ("shortnews", "⚡", "오늘의 퀵뉴스", build_shortnews()),
        ("politics_comment", "🏛️", "정치 논평 (AI)", build_politics_comment()),
        ("economy_comment", "💹", "경제 논평 (AI)", build_economy_comment()),
        ("subs", "🏗️", "청약 소식", build_subs()),
        ("trend", "📈", "부동산 주간 시세동향", build_trend()),
        ("fuelfx", "⛽", "기름값·환율", build_fuelfx()),
        ("metalcoin", "🥇", "금·은·코인", build_metalcoin()),
        ("books", "📚", "주간 베스트셀러", build_books()),
        ("realestate", "🏠", "부동산 뉴스", build_realestate_news()),
        ("realestate_comment", "🏠", "부동산 시장 논평 (AI)", build_realestate_comment()),
        ("world", "🌏", "세계 뉴스", build_world_news()),
        ("world_comment", "🌐", "국제정세 논평 (AI)", build_world_comment()),
        ("finance", "🏦", "금융 뉴스", build_finance_news()),
        ("finance_comment", "🏦", "금융시장 논평 (AI)", build_finance_comment()),
        ("ai", "🤖", "AI 뉴스", build_ai_news()),
        ("ai_comment", "🤖", "AI·테크 논평 (AI)", build_ai_comment()),
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
