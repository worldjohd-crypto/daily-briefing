"""Public-source additions. No AI clients, credentials or paid APIs."""
import datetime
import json
import html
import re
from urllib.parse import urlparse

EXTRA = [
    ('daily19', '⭕', '오늘의 부동산 OX'),
    ('daily20', '🏠', '집 보러 갈 때 한 가지'),
    ('daily21', '💬', '부동산 광고 해석기'),
    ('daily22', '🧠', '돈에 관한 착각'),
    ('daily23', '🛠️', '오늘의 집 관리'),
    ('daily24', '📦', '이사 준비 한 가지'),
    ('transactions', '🏢', '전국 아파트 실거래가'),
    ('seoulcommerce', '📍', '서울 주요 상권'),
    ('cryptofear', '📊', '비트코인 공포·탐욕 지수'),
    ('wikiinterest', '📖', '경제 주제 읽기 관심도'),
]
NEWS = [
    ('entertainment', '🌟', '연예 뉴스', '연예 배우 가수'),
    ('romance', '💕', '열애·결혼 소식', '(열애 OR 결혼 OR 결별) (연예 OR 배우 OR 가수)'),
    ('broadcast', '📺', '방송 화제', '드라마 예능 방송 화제'),
]

NEWS_HEADINGS = {
    'shortnews': ('⚡', '오늘의 퀵뉴스'),
    'realestate': ('🏠', '부동산 주요뉴스'),
    'world': ('🌏', '세계 뉴스'),
    'finance': ('🏦', '금융 뉴스'),
    'ai': ('🤖', 'AI 뉴스'),
    'entertainment': ('🌟', '연예 뉴스'),
    'romance': ('💕', '열애·결혼 소식'),
    'broadcast': ('📺', '방송 화제'),
}


def format_news_heading(section_id, body, now):
    if section_id not in NEWS_HEADINGS:
        return body
    emoji, label = NEWS_HEADINGS[section_id]
    weekday = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일'][now.weekday()]
    heading = f'{emoji} [{label}]\n{now.year % 100}년 {now.month}월 {now.day}일 {weekday}'
    parts = body.split('\n\n', 1)
    # Replace only the generated header, preserving article text and failures.
    if re.search(r'\d{2,4}(?:년|-)\s*\d{1,2}(?:월|-)', parts[0]):
        body = parts[1] if len(parts) > 1 else ''
    return heading + '\n\n' + body


def is_naver_article(url):
    parsed = urlparse(url)
    return (parsed.scheme == 'https' and parsed.hostname in
            {'news.naver.com', 'n.news.naver.com', 'm.entertain.naver.com', 'entertain.naver.com'}
            and re.search(r'/article/\d+/\d+', parsed.path) is not None)


def prepare_news_text(body, section_id):
    """Publish original Naver article URLs, with identical clipboard text."""
    blocks = []
    for block in body.split('\n\n'):
        lines = block.splitlines()
        if len(lines) in (2, 3) and urlparse(lines[-1]).scheme == 'https':
            if is_naver_article(lines[-1]):
                blocks.append(lines[0] + '\n' + lines[-1])
        else:
            blocks.append(block)
    return '\n\n'.join(blocks)


def render_news_links(body):
    """Keep original copy text separate; render only validated headline links."""
    blocks = []
    for block in body.split('\n\n'):
        lines = block.splitlines()
        if len(lines) in (2, 3):
            title, url = lines[0], lines[-1]
            parsed = urlparse(url)
            if is_naver_article(url):
                blocks.append('<a href="' + html.escape(url, quote=True)
                              + '" target="_blank" rel="noopener noreferrer">'
                              + html.escape(title) + '</a>')
                continue
        blocks.append(html.escape(block))
    return '\n\n'.join(blocks)


ROMANCE_NEWS_RE = re.compile(r'열애|결혼|결별|♥|부부|연인|이혼|재혼|신혼|연애|상견례')
BROADCAST_NEWS_RE = re.compile(r'예능|드라마|시청률|방송|OTT|넷플릭스|놀면|놀뭐|나 혼자|런닝맨|미우새|돌싱포맨|쇼미|환승연애|나는 솔로|살림남|미스터트롯|불후|금타는|닥터|비서진|출연|나혼산')
AD_WALL_RE = re.compile(r'goog-continue-reading-btn|계속하려면\s*광고|광고를\s*시청.{0,20}계속', re.I)


_NAVER_ENTERTAINMENT = None


def naver_entertainment(get, now):
    global _NAVER_ENTERTAINMENT
    if _NAVER_ENTERTAINMENT is None:
        items = []
        for days in (0, 1):
            date = (now - datetime.timedelta(days=days)).strftime('%Y%m%d')
            response = get('https://api-gw.entertain.naver.com/news/articles',
                           params={'date': date, 'page': 1, 'pageSize': 100}, timeout=20)
            response.raise_for_status()
            data = response.json()
            if data.get('success'):
                items.extend(data.get('result', {}).get('newsList', []))
        _NAVER_ENTERTAINMENT = items
    return _NAVER_ENTERTAINMENT


def news_text(get, now, label, query, used):
    blocks = [f'{now:%Y-%m-%d %H:%M} 수집 · {label}']
    count = 0
    for item in naver_entertainment(get, now):
        title, link = html.unescape(item.get('title', '')).strip(), item.get('url', '')
        if not title or title in used or not is_naver_article(link):
            continue
        if AD_WALL_RE.search(item.get('subContent', '')):
            continue
        if '열애' in label and not ROMANCE_NEWS_RE.search(title):
            continue
        if '방송' in label and not BROADCAST_NEWS_RE.search(title):
            continue
        used.add(title)
        blocks.append(title + '\n' + link)
        count += 1
        if count == 8:
            break
    if not count:
        blocks.append('조건에 맞는 네이버 기사를 확인하지 못했습니다. 다음 자동 업데이트에서 다시 확인합니다.')
    return '\n\n'.join(blocks)


def extend_sections(sections, get, now):
    from official_guides import build_guides
    from property_resources import build_property_resources
    from official_metrics import build_cryptofear, build_wikiinterest
    from official_housing import build_transactions, build_seoulcommerce
    from housing_history import build_history_comparison
    from housing_story import build_story_section

    # Reuse the already collected headlines, leaving the original news untouched.
    news_position = next((i for i, value in enumerate(sections) if value[0] == 'realestate'), None)
    if news_position is not None:
        try:
            history = build_history_comparison(sections[news_position][3], get, now)
        except Exception:
            history = '과거 뉴스·집값 비교 자료 확인에 실패했습니다. 다음 무료 자동 갱신에서 다시 확인합니다. 수치를 추정하지 않습니다.'
        sections.insert(news_position + 1, ('historycompare', '🕰️', '과거 상승기 뉴스·집값 비교', history))
        story = build_story_section(sections[news_position][3], get, now)
        sections.insert(news_position + 2, ('housingstory', '📚', '실거래로 읽는 투자 이야기', story))

    # Every extra reads its provider directly; unavailable sources never use a mirror.
    try:
        bodies = build_guides(get, now)
    except Exception:
        bodies = {}
    for sid, builder in (('transactions', build_transactions),
                         ('seoulcommerce', build_seoulcommerce),
                         ('cryptofear', build_cryptofear), ('wikiinterest', build_wikiinterest)):
        try:
            body = builder(get, now)
        except Exception:
            body = None
        if body:
            bodies[sid] = body
    for sid, emoji, label in EXTRA:
        if bodies.get(sid):
            sections.append((sid, emoji, label, bodies[sid]))
    try:
        sections.extend(build_property_resources(get, now))
    except Exception:
        pass
    used, news_bodies = set(), {}
    for sid, emoji, label, query in (NEWS[1], NEWS[2], NEWS[0]):
        try:
            body = news_text(get, now, label, query, used)
        except Exception:
            body = '뉴스 수집에 실패했습니다. 다음 무료 자동 갱신에서 다시 확인합니다.'
        news_bodies[sid] = body
    for sid, emoji, label, query in NEWS:
        sections.append((sid, emoji, label, news_bodies[sid]))
    return sections
