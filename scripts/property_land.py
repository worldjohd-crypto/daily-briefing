"""Latest public land-use notices from MOLIT's free Eum website.

This is a dated nationwide notice list, never a parcel-specific zoning decision.
The public server-rendered page does not require an account or API key.
"""

import datetime as dt
import re
import sys
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup


KST = dt.timezone(dt.timedelta(hours=9))
EUM_LIST = 'https://www.eum.go.kr/web/gs/gv/gvGosiList.jsp'
EUM_DETAIL = 'https://www.eum.go.kr/web/gs/gv/gvGosiDet.jsp?seq='
USER_AGENT = 'DailyBriefing/1.0 (https://worldjohd-crypto.github.io/daily-briefing/) requests'
SOURCES = {
    'landcheck': {
        'provider': '국토교통부 토지이음', 'source': EUM_LIST,
        'cost': 'free_public_information', 'keyRequired': False,
        'collection': 'public_server_rendered_html',
        'scope': 'nationwide first public list page; last 30 calendar days; up to five dated official notices; not parcel-specific advice',
    },
}


def _clean(value):
    return re.sub(r'\s+', ' ', value).strip()


def _notice(row, begin, today):
    cells = row.find_all('td', recursive=False)
    if len(cells) != 4:
        return None
    stamp = cells[0].get_text(' ', strip=True)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', stamp):
        return None
    try:
        published = dt.date.fromisoformat(stamp)
    except ValueError:
        return None
    if not begin <= published <= today:
        return None
    anchor = cells[2].find('a', href=True)
    if anchor is None:
        return None
    link = urlsplit(urljoin(EUM_LIST, anchor['href']))
    if (link.scheme != 'https' or link.netloc != 'www.eum.go.kr'
            or link.path != '/web/gs/gv/gvGosiDet.jsp'):
        return None
    identities = parse_qs(link.query).get('seq', [])
    if len(identities) != 1 or not re.fullmatch(r'\d{1,12}', identities[0]):
        return None
    identity = identities[0]
    title = _clean(anchor.get_text(' ', strip=True))
    number = _clean(cells[1].get_text(' ', strip=True))
    agency = _clean(cells[3].get_text(' ', strip=True))
    if not title or not number or not agency:
        return None
    return identity, published, '\n'.join([
        title,
        f'고시일 {published:%Y-%m-%d} · {number}',
        '담당기관: ' + agency,
        EUM_DETAIL + identity,
    ])


def build_land_updates(get, now):
    """Return verified recent titles, dates, issuing bodies and original links."""
    now = (now.replace(tzinfo=KST) if now.tzinfo is None else now).astimezone(KST)
    begin = now.date() - dt.timedelta(days=29)
    source = '자료: 국토교통부 토지이음 · 전국 공식 고시정보\n' + EUM_LIST
    explanation = ('전국의 토지이용·도시계획 관련 공식 고시를 소개합니다. '
                   '담당기관과 고시번호로 지역을 구분해 원문·도면을 확인하세요. '
                   '이 목록은 개별 필지의 적용 규제나 건축 가능 여부를 판정한 결과가 아닙니다.')
    try:
        response = get(EUM_LIST, params={'startdt': begin.isoformat(), 'enddt': now.date().isoformat()},
                       headers={'User-Agent': USER_AGENT}, timeout=25)
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('Eum public list did not return HTTP 200')
        # The actual HTTP response is EUC-KR despite an incorrect UTF-8 meta tag.
        response.encoding = 'euc-kr'
        soup = BeautifulSoup(response.text, 'html.parser')
        expected = ['고시일', '고시번호', '[구분]고시제목', '담당기관']
        tables = [table for table in soup.find_all('table')
                  if [_clean(cell.get_text(' ', strip=True)) for cell in table.select('thead th')] == expected]
        if len(tables) != 1:
            raise ValueError('Eum public table schema changed')
        table = tables[0]
        rows = table.select('tbody tr')
        empty = (len(rows) == 1 and len(rows[0].find_all('td', recursive=False)) == 1
                 and _clean(rows[0].get_text(' ', strip=True)) == '데이터가 없습니다.')
        notices, seen = [], set()
        for row in rows:
            item = _notice(row, begin, now.date())
            if item and item[0] not in seen:
                seen.add(item[0])
                notices.append(item)
        if not notices and not empty:
            raise ValueError('No verifiable dated Eum notices in response')
        notices.sort(key=lambda item: item[1], reverse=True)
        lines = [f'고시일 기준: {begin:%Y-%m-%d} ~ {now:%Y-%m-%d} · 전국 공식 목록 첫 페이지 · 최대 5건',
                 explanation]
        lines.extend([item[2] for item in notices[:5]] or ['최근 30일 조회 조건에 해당하는 공식 고시가 없습니다.'])
        lines.extend(['계약 전에는 정확한 주소로 토지이용계획을 열람하고 적용 시점·관련 도면·담당 지자체 확인까지 진행하세요.',
                      f'수집: {now:%Y-%m-%d %H:%M} (한국시간)', source])
        return '\n\n'.join(lines)
    except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f'[WARN] Eum land-use notices unavailable ({type(exc).__name__}).', file=sys.stderr)
        return '\n\n'.join([
            '최신 공식 토지·도시계획 고시 목록을 확인하지 못했습니다. 이전 고시를 최신 정보로 표시하지 않습니다.',
            explanation,
            f'확인 시도: {now:%Y-%m-%d %H:%M} (한국시간)',
            source,
        ])
