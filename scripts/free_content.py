"""Public-source additions. No AI clients, credentials or paid APIs."""
import datetime
import json
import html
import hashlib
from bs4 import BeautifulSoup
import requests
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse
from pathlib import Path

SOURCE = 'https://busanfransisco-source.github.io/naver-realestate-bot/briefing.html'
EXTRA = [
    ('daily19', '⭕', '오늘의 부동산 OX'),
    ('daily20', '🏠', '집 보러 갈 때 한 가지'),
    ('daily21', '💬', '부동산 광고 해석기'),
    ('daily22', '🧠', '돈에 관한 착각'),
    ('daily23', '🛠️', '오늘의 집 관리'),
    ('daily24', '📦', '이사 준비 한 가지'),
    ('transactions', '🏢', '전국 신규 등록 실거래가'),
    ('seoulcommerce', '📍', '서울 주요 상권'),
    ('cryptofear', '📊', '비트코인 공포·탐욕 지수'),
    ('wikiinterest', '📖', '경제 주제 읽기 관심도'),
]
NEWS = [
    ('entertainment', '🌟', '연예 뉴스', '연예 배우 가수'),
    ('romance', '💕', '열애·결혼 소식', '(열애 OR 결혼 OR 결별) (연예 OR 배우 OR 가수)'),
    ('broadcast', '📺', '방송 화제', '드라마 예능 방송 화제'),
]

ARTICLE_LINKS_FILE = Path(__file__).resolve().parent.parent / 'article-links.json'
SHORT_LINK_BASE = 'https://worldjohd-crypto.github.io/daily-briefing/go.html#'
EXTERNAL_LINKS_FILE = Path(__file__).resolve().parent.parent / 'external-shortlinks.json'
_SHORTENER_UNAVAILABLE = False
PORTAL_NEWS_HOSTS = {'v.daum.net'}
NEWS_SOURCE_AUDIT_FILE = Path(__file__).resolve().parent.parent / 'news-source-audit.json'


def external_article_link(url):
    """Cache verified external redirects. On failure use the article, never this site."""
    global _SHORTENER_UNAVAILABLE
    if re.fullmatch(r'https://da\.gd/[A-Za-z0-9_-]+', url):
        return url
    if urlparse(url).scheme != 'https' or urlparse(url).hostname not in ('news.google.com', 'v.daum.net'):
        return url
    try:
        links = json.loads(EXTERNAL_LINKS_FILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        links = {}
    cached = links.get(url, '')
    if re.fullmatch(r'https://da\.gd/[A-Za-z0-9_-]+', cached):
        return cached
    if _SHORTENER_UNAVAILABLE:
        return url
    try:
        response = requests.get('https://da.gd/s', params={'url': url}, timeout=20)
        response.raise_for_status()
        short = response.text.strip()
        if not re.fullmatch(r'https://da\.gd/[A-Za-z0-9_-]+', short):
            raise ValueError('Invalid shortener response')
        target = requests.get(short, allow_redirects=False, timeout=20)
        if target.status_code not in (301, 302, 303, 307, 308) or target.headers.get('Location') != url:
            raise ValueError('Unexpected redirect destination')
        links[url] = short
        EXTERNAL_LINKS_FILE.write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding='utf-8')
        return short
    except (requests.RequestException, ValueError):
        _SHORTENER_UNAVAILABLE = True
        return url

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
    heading = f'{emoji} {now.year % 100}년 {now.month}월 {now.day}일 {weekday} [{label}]'
    parts = body.split('\n\n', 1)
    # Replace only the generated header, preserving article text and failures.
    if re.search(r'\d{2,4}(?:년|-)\s*\d{1,2}(?:월|-)', parts[0].splitlines()[0]):
        body = parts[1] if len(parts) > 1 else ''
    return heading + '\n\n' + body


def local_article_link(url):
    # Keep old destinations forever; daily refreshes only add new entries.
    if url.startswith(SHORT_LINK_BASE):
        return url
    if urlparse(url).scheme != 'https' or urlparse(url).hostname != 'news.google.com':
        return url
    try:
        registry = json.loads(ARTICLE_LINKS_FILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        registry = {}
    digest = hashlib.sha256(url.encode()).hexdigest()
    size = 10
    while digest[:size] in registry and registry[digest[:size]] != url:
        size += 2
    key = digest[:size]
    if key not in registry:
        registry[key] = url
        ARTICLE_LINKS_FILE.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf-8')
    return SHORT_LINK_BASE + key


def prepare_news_text(body, section_id):
    original_links = {}
    if section_id in ('entertainment', 'romance', 'broadcast'):
        try:
            original_links = {v: k for k, v in json.loads((Path(__file__).resolve().parent.parent / 'news-shortlinks.json').read_text(encoding='utf-8')).items()}
        except (OSError, ValueError):
            pass
        # Recover original destinations from already-published local short links.
        try:
            original_links.update({SHORT_LINK_BASE + k: v for k, v in json.loads(ARTICLE_LINKS_FILE.read_text(encoding='utf-8')).items()})
        except (OSError, ValueError):
            pass
    blocks = []
    for block in body.split('\n\n'):
        lines = block.splitlines()
        if len(lines) in (2, 3) and urlparse(lines[-1]).scheme == 'https':
            url = original_links.get(lines[-1], lines[-1])
            if section_id in ('entertainment', 'romance', 'broadcast'):
                url = external_article_link(url)
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname not in ('news.google.com', 'v.daum.net', 'm.site.naver.com', 'da.gd'):
                url = lines[-1]
            blocks.append(lines[0] + '\n' + url)
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
            if parsed.scheme == 'https' and parsed.hostname in ('news.google.com', 'v.daum.net', 'm.site.naver.com', 'da.gd'):
                blocks.append('<a href="' + html.escape(url, quote=True)
                              + '" target="_blank" rel="noopener noreferrer">'
                              + html.escape(title) + '</a>')
                continue
        blocks.append(html.escape(block))
    return '\n\n'.join(blocks)


def source_sections(get, now):
    """Parse JSON literals only. Preserve original dates; reject old editions."""
    response = get(SOURCE, timeout=25)
    response.encoding = 'utf-8'
    page = response.text
    if not re.search(r'<h1[^>]*>[^<]*' + re.escape(f'{now.year}년 {now.month}월 {now.day}일'), page):
        return {}
    result = {}
    for sid in [x[0] for x in EXTRA] + ['fuelfx', 'metalcoin', 'trend']:
        match = re.search(r'window\.__content_' + sid + r'\s*=\s*("(?:[^"\\]|\\.)*")\s*;', page)
        if match:
            value = json.loads(match.group(1)).strip()
            if value and not re.search('수집 중|수집중|업데이트 예정|데이터를 가져오지 못', value):
                result[sid] = value + '\n\n자료: ' + SOURCE
    return result


_PORTAL_CANDIDATES = None
_VERIFIED_PORTAL_ARTICLES = {}
ROMANCE_NEWS_RE = re.compile(r'열애|결혼|결별|♥|부부|연인|이혼|재혼|신혼|연애|상견례')
BROADCAST_NEWS_RE = re.compile(r'예능|드라마|시청률|방송|OTT|넷플릭스|놀면|놀뭐|나 혼자|런닝맨|미우새|돌싱포맨|쇼미|환승연애|나는 솔로|살림남|미스터트롯|불후|금타는|닥터|비서진|출연|나혼산')
AD_WALL_RE = re.compile(r'goog-continue-reading-btn|계속하려면\s*광고|광고를\s*시청.{0,20}계속', re.I)


def _portal_candidates(get):
    global _PORTAL_CANDIDATES
    if _PORTAL_CANDIDATES is None:
        response = get('https://entertain.daum.net/news', timeout=20)
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        items, seen = [], set()
        for anchor in soup.find_all('a', href=True):
            url = anchor['href']
            match = re.fullmatch(r'https://v\.daum\.net/v/(\d{14})\d*', url)
            title = anchor.select_one('.tit_txt')
            if not match or not title or url in seen:
                continue
            seen.add(url)
            items.append((title.get_text(' ', strip=True), url, match.group(1)))
        # The portal also supplies more stories as quoted hydration data.
        # Parse string literals only; never execute embedded JavaScript.
        pattern = r'\{id:"hamny-\d+",type:"TEXT",title:("(?:[^"\\]|\\.)*")[\s\S]*?pcUrl:("(?:[^"\\]|\\.)*")'
        for match in re.finditer(pattern, response.text):
            try:
                title, url = (json.loads(value) for value in match.groups())
            except ValueError:
                continue
            stamp = re.fullmatch(r'https://v\.daum\.net/v/(\d{14})\d*', url)
            if stamp and url not in seen:
                seen.add(url)
                items.append((title, url, stamp.group(1)))
        _PORTAL_CANDIDATES = sorted(items, key=lambda x: x[2], reverse=True)
    return _PORTAL_CANDIDATES


def _verify_portal_article(get, url):
    if url not in _VERIFIED_PORTAL_ARTICLES:
        try:
            response = get(url, timeout=15)
            response.encoding = 'utf-8'
            if urlparse(response.url).hostname != 'v.daum.net' or AD_WALL_RE.search(response.text):
                _VERIFIED_PORTAL_ARTICLES[url] = None
            else:
                soup = BeautifulSoup(response.text, 'html.parser')
                body = soup.select_one('.article_view, #harmonyContainer, article')
                title = soup.select_one('meta[property="og:title"]')
                if not body or len(body.get_text(' ', strip=True)) < 80:
                    _VERIFIED_PORTAL_ARTICLES[url] = None
                else:
                    _VERIFIED_PORTAL_ARTICLES[url] = {'finalUrl': response.url, 'title': title.get('content', '') if title else '', 'adWallDetected': False}
        except Exception:
            _VERIFIED_PORTAL_ARTICLES[url] = None
    return _VERIFIED_PORTAL_ARTICLES[url]


def news_text(get, now, label, query, used):
    lines = [f'{now:%Y-%m-%d %H:%M} 수집 · {label}', '']
    count = 0
    try:
        source_audit = json.loads(NEWS_SOURCE_AUDIT_FILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        source_audit = {}
    for title, link, stamp in _portal_candidates(get):
        published = datetime.datetime.strptime(stamp, '%Y%m%d%H%M%S').replace(tzinfo=now.tzinfo)
        if not 0 <= (now - published).total_seconds() <= 172800 or title in used:
            continue
        if '열애' in label and not ROMANCE_NEWS_RE.search(title):
            continue
        if '방송' in label and not BROADCAST_NEWS_RE.search(title):
            continue
        checked = _verify_portal_article(get, link)
        if not checked:
            continue
        title = checked['title'] or title
        if title in used:
            continue
        used.add(title)
        source_audit[link] = {'title': title, 'sourceUrl': 'https://v.daum.net', 'collectedAt': now.isoformat(), 'policy': 'direct_portal_no_detected_ad_wall', **checked}
        lines.extend([title, link, ''])
        count += 1
        if count == 8:
            break
    if not count:
        lines.append('최근 48시간 내 열람 조건에 맞는 포털 기사가 없습니다. 광고 시청 유도 기사나 일반 언론사 사이트로 대체하지 않습니다.')
    NEWS_SOURCE_AUDIT_FILE.write_text(json.dumps(source_audit, ensure_ascii=False, indent=2), encoding='utf-8')
    return '\n'.join(lines).strip()


def extend_sections(sections, get, now):
    try:
        upstream = source_sections(get, now)
    except Exception:
        upstream = {}
    # Only replace failed free market data, never a commentary section.
    sections = [(sid, emoji, title, upstream.get(sid, body)
                 if sid in ('fuelfx', 'metalcoin', 'trend') and (body == '데이터를 가져오지 못했습니다.' or '설정되지 않아' in body) else body)
                for sid, emoji, title, body in sections]
    for sid, emoji, label in EXTRA:
        sections.append((sid, emoji, label, upstream.get(sid, '오늘 자료를 확인하지 못했습니다. 이전 날짜 자료를 오늘 정보로 표시하지 않습니다.\n자료: ' + SOURCE)))
    used, news_bodies = set(), {}
    # Reserve topic-specific stories before choosing general entertainment news.
    for sid, emoji, label, query in (NEWS[1], NEWS[2], NEWS[0]):
        try:
            body = news_text(get, now, label, query, used)
        except Exception:
            body = '뉴스 수집에 실패했습니다. 다음 무료 자동 갱신에서 다시 확인합니다.'
        news_bodies[sid] = body
    for sid, emoji, label, query in NEWS:
        sections.append((sid, emoji, label, news_bodies[sid]))
    return sections
