"""Public-source additions. No AI clients, credentials or paid APIs."""
import datetime
import json
import html
import hashlib
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
    ('romance', '💕', '열애·결혼 소식', '연예인 열애 결혼 결별'),
    ('broadcast', '📺', '방송 화제', '드라마 예능 방송 화제'),
]

ARTICLE_LINKS_FILE = Path(__file__).resolve().parent.parent / 'article-links.json'
SHORT_LINK_BASE = 'https://worldjohd-crypto.github.io/daily-briefing/go.html#'
EXTERNAL_LINKS_FILE = Path(__file__).resolve().parent.parent / 'external-shortlinks.json'
_SHORTENER_UNAVAILABLE = False


def external_article_link(url):
    """Cache verified external redirects. On failure use the article, never this site."""
    global _SHORTENER_UNAVAILABLE
    if re.fullmatch(r'https://da\.gd/[A-Za-z0-9_-]+', url):
        return url
    if urlparse(url).scheme != 'https' or urlparse(url).hostname != 'news.google.com':
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
    if section_id in ('entertainment', 'romance'):
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
            if section_id in ('entertainment', 'romance'):
                url = external_article_link(url)
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname not in ('news.google.com', 'm.site.naver.com', 'da.gd'):
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
            if parsed.scheme == 'https' and parsed.hostname in ('news.google.com', 'm.site.naver.com', 'da.gd'):
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


def news_text(get, now, label, query, used):
    url = 'https://news.google.com/rss/search?q=' + quote(query + ' when:2d') + '&hl=ko&gl=KR&ceid=KR:ko'
    root = ET.fromstring(get(url, timeout=20).content)
    lines = [f'{now:%Y-%m-%d %H:%M} 수집 · {label}', '기사 제목·출처·원문 링크 (AI 생성 없음)', '']
    count = 0
    for item in root.findall('./channel/item'):
        title = item.findtext('title', '').strip()
        link = item.findtext('link', '').strip()
        try:
            published = parsedate_to_datetime(item.findtext('pubDate', '')).astimezone(now.tzinfo)
        except (ValueError, TypeError, OverflowError):
            continue
        if not title or title in used or not 0 <= (now - published).total_seconds() <= 172800:
            continue
        if urlparse(link).scheme != 'https' or urlparse(link).hostname != 'news.google.com':
            continue
        used.add(title)
        source = item.findtext('source', '언론사 미표시')
        lines.extend([title, f'{source} · {published:%m/%d %H:%M}', link, ''])
        count += 1
        if count == 8:
            break
    if not count:
        lines.append('최근 48시간 내 수집된 기사가 없습니다.')
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
    used = set()
    for sid, emoji, label, query in NEWS:
        try:
            body = news_text(get, now, label, query, used)
        except Exception:
            body = '뉴스 수집에 실패했습니다. 다음 무료 자동 갱신에서 다시 확인합니다.'
        sections.append((sid, emoji, label, body))
    return sections
