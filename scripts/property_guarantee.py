"""Current HUG return-guarantee facts from its free, public product page.

No account, OpenAPI key, quotation or AI client is used. The product table is
read again on every build; an outage/schema change never becomes a dated guide.
"""

import datetime as dt
import re
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from bs4 import BeautifulSoup


KST = dt.timezone(dt.timedelta(hours=9))
HUG_SOURCE = 'https://www.khug.or.kr/hug/web/ig/dr/igdr000001.jsp?tabId=1'
USER_AGENT = 'DailyBriefing/1.0 (https://worldjohd-crypto.github.io/daily-briefing/) requests'
SOURCES = {
    'rentalguarantee': {
        'provider': 'HUG 주택도시보증공사', 'source': HUG_SOURCE,
        'cost': 'free_public_information', 'keyRequired': False,
        'collection': 'public_product_table_and_linked_notice',
        'scope': 'current application deadline, eligible housing and deposit conditions; '
                 'optional conversion-rate notice linked by the product page',
        'dates': 'collection time is distinct from optional notice publication date; '
                 'product page itself has no stated publication date',
    },
}


def _text(node):
    return re.sub(r'\s+', ' ', node.get_text(' ', strip=True)).strip()


def _official_soup(get, url):
    response = get(url, headers={'User-Agent': USER_AGENT}, timeout=25)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('HUG public page did not return HTTP 200')
    actual = urlsplit(response.url)
    if actual.scheme != 'https' or actual.hostname not in ('khug.or.kr', 'www.khug.or.kr'):
        raise ValueError('HUG public page redirected outside the official source')
    # BeautifulSoup respects the page's EUC-KR declaration, rather than relying
    # on Requests' default charset or forcing a charset on future UTF-8 pages.
    return BeautifulSoup(response.content, 'html.parser')


def _rows(table):
    """Retain all rowspan notes for a selected field, including exclusions."""
    fields = {}
    active = None
    remaining = 0
    for row in table.find_all('tr'):
        cells = row.find_all(['th', 'td'], recursive=False)
        if len(cells) == 2 and cells[0].name == 'th' and cells[1].name == 'td':
            if remaining:
                raise ValueError('Incomplete HUG rowspan')
            active = re.sub(r'\s+', '', _text(cells[0]))
            if active in fields:
                raise ValueError('Duplicate HUG product field')
            remaining = int(cells[0].get('rowspan', 1)) - 1
            if not 0 <= remaining <= 3:
                raise ValueError('Unexpected HUG rowspan')
            fields[active] = [cells[1]]
        elif len(cells) == 1 and cells[0].name == 'td' and active and remaining:
            fields[active].append(cells[0])
            remaining -= 1
        else:
            raise ValueError('Unexpected HUG product table row')
    if remaining:
        raise ValueError('Incomplete HUG rowspan')
    return fields


def _notice_url(anchor):
    """Reconstruct only the known official public notice route, never its href."""
    if anchor is None:
        return None
    parts = urlsplit(anchor.get('href', ''))
    if (parts.scheme != 'https' or parts.hostname not in ('khug.or.kr', 'www.khug.or.kr')
            or parts.username or parts.password or parts.port or parts.fragment
            or parts.path != '/hug/web/cs/no/csno000001.jsp'):
        return None
    query = parse_qs(parts.query)
    identity = query.get('articleId', [''])[0]
    if query.get('id') != ['16'] or query.get('mode') != ['S'] or not re.fullmatch(r'\d{1,12}', identity):
        return None
    return ('https://www.khug.or.kr/hug/web/cs/no/csno000001.jsp?'
            + urlencode(dict(id='16', mode='S', currentPage='1', articleId=identity)))


def _linked_notice(get, url, now):
    soup = _official_soup(get, url)
    fields = {}
    for row in soup.select('li.tit'):
        label, value = row.find('strong', recursive=False), row.find('div', recursive=False)
        if label and value:
            key = _text(label)
            if key in ('제목', '작성일'):
                if key in fields:
                    raise ValueError('Duplicate HUG notice field')
                fields[key] = _text(value)
    title, date = fields['제목'], fields['작성일']
    if ('전월세전환율' not in title or '전세보증' not in title
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date)):
        raise ValueError('Unexpected HUG linked notice')
    published = dt.date.fromisoformat(date)
    if published > now.date() or not 5 <= len(title) <= 180:
        raise ValueError('Invalid HUG linked notice date/title')
    return f'상품 페이지 연결 공지: {title} · 공지 작성일 {published:%Y-%m-%d}'


def build_rental_guarantee(get, now):
    """Fetch current source facts, or return a visible unavailable state."""
    now = (now.replace(tzinfo=KST) if now.tzinfo is None else now).astimezone(KST)
    footer = '자료: HUG 주택도시보증공사 · 전세보증금반환보증 공식 상품 안내\n' + HUG_SOURCE
    try:
        soup = _official_soup(get, HUG_SOURCE)
        tables = [table for table in soup.find_all('table')
                  if table.find('caption') and
                  '전세보증금반환보증상품개요' in re.sub(r'\s+', '', _text(table.find('caption')))]
        if len(tables) != 1:
            raise ValueError('Missing/ambiguous HUG product table')
        fields = _rows(tables[0])
        deadline = ' '.join(_text(n) for n in fields['보증신청기한'])
        housing = ' '.join(_text(n) for n in fields['보증대상주택'])
        condition_cells = fields['보증조건']
        candidates = [item for cell in condition_cells for item in cell.find_all('li')
                      if re.match(r'^[①-⑳\d.)\s]*전세보증금\(', re.sub(r'\s+', '', _text(item)))]
        if len(candidates) != 1:
            raise ValueError('Missing/ambiguous HUG deposit requirement')
        deposit_node = candidates[0]
        linked = [a for a in deposit_node.find_all('a', href=True) if '전월세전환율' in _text(a)]
        notice_url = _notice_url(linked[0]) if len(linked) == 1 else None
        # Remove a navigation instruction, retaining the rate and effective-date
        # conditions immediately before it, exactly as the current page states.
        deposit = _text(deposit_node).replace('← 클릭시 공지사항으로 이동', '').strip()
        if (not 20 <= len(deadline) <= 600 or not 10 <= len(housing) <= 1000
                or not 20 <= len(deposit) <= 1000
                or '전세계약' not in deadline or '주택' not in housing or '보증금' not in deposit):
            raise ValueError('Incomplete HUG product facts')
        lines = [
            'HUG 공식 상품 안내에 현재 표시된 가입 요건 중 일부입니다. 전체 심사조건·보증료는 원문에서 확인하세요.',
            '신청기한: ' + deadline,
            '대상주택: ' + housing,
            '보증금 요건: ' + deposit,
        ]
        if notice_url:
            try:
                lines.append(_linked_notice(get, notice_url, now))
            except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError):
                lines.append('상품 페이지에 연결된 공지의 제목·작성일은 확인하지 못했습니다. 위 가입 요건은 이번에 수집한 상품 페이지 표시 기준입니다.')
            footer += '\n' + notice_url
        else:
            lines.append('상품 페이지에 연결된 전월세전환율 공지를 확인하지 못했습니다.')
        lines.extend([
            '상품 페이지에 별도 게시일·수정일이 없어 수집 시각을 정책 시행일로 표시하지 않습니다.',
            f'수집: {now:%Y-%m-%d %H:%M} (한국시간)',
            footer,
        ])
        return '\n\n'.join(lines)
    except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f'[WARN] HUG current guarantee facts unavailable ({type(exc).__name__}).', file=sys.stderr)
        return '\n\n'.join([
            '최신 HUG 전세보증금반환보증 가입 요건을 확인하지 못했습니다. 이전 조건을 오늘 확인한 최신 정보로 표시하지 않습니다.',
            '가입 전 HUG 공식 상품 안내에서 신청기한·대상주택·보증금 및 전체 보증조건을 확인하세요.',
            f'확인 시도: {now:%Y-%m-%d %H:%M} (한국시간)', footer,
        ])
