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
MOLIT_LIST = 'https://www.molit.go.kr/USR/BORD0201/m_69/BRD.jsp'
MOLIT_DETAIL = 'https://www.molit.go.kr/USR/BORD0201/m_69/DTL.jsp?id=N01_B&mode=view&idx='
LAND_TOPIC = re.compile(r'토지|용도지역|지구단위|도시(?:관리|계획|재생|개발)|국토계획|공공주택|택지개발|정비(?:계획|구역)|주거환경|재개발|재건축')
USER_AGENT = 'DailyBriefing/1.0 (https://worldjohd-crypto.github.io/daily-briefing/) requests'
SOURCES = {
    'landcheck': {
        'provider': '국토교통부 토지이음', 'source': EUM_LIST,
        'cost': 'free_public_information', 'keyRequired': False,
        'collection': 'public_server_rendered_html',
        'scope': 'nationwide first public list page; last 30 calendar days; up to five dated official notices; not parcel-specific advice',
        'fallback': {
            'provider': '국토교통부', 'source': MOLIT_LIST,
            'cost': 'free_public_information', 'keyRequired': False,
            'collection': 'public_server_rendered_html',
            'scope': 'MOLIT notice board first page filtered to land, urban planning and housing project notices; last 30 calendar days; not all local-government notices',
        },
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


def _molit_notice(row, begin, today):
    cells = row.find_all('td', recursive=False)
    if len(cells) != 5:
        return None
    stamp = _clean(cells[2].get_text(' ', strip=True))
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', stamp):
        return None
    try:
        published = dt.date.fromisoformat(stamp)
    except ValueError:
        return None
    if not begin <= published <= today:
        return None
    anchor = cells[1].find('a', href=True)
    if anchor is None:
        return None
    link = urlsplit(urljoin(MOLIT_LIST, anchor['href']))
    if (link.scheme != 'https' or link.netloc != 'www.molit.go.kr'
            or link.path != '/USR/BORD0201/m_69/DTL.jsp'):
        return None
    query = parse_qs(link.query)
    identities = query.get('idx', [])
    if (query.get('id') != ['N01_B'] or query.get('mode') != ['view']
            or len(identities) != 1 or not re.fullmatch(r'\d{1,12}', identities[0])):
        return None
    title = _clean(anchor.get_text(' ', strip=True))
    department = _clean(cells[3].get_text(' ', strip=True))
    if not title or not department:
        return None
    return identities[0], published, title, department


def _molit_updates(get, now, begin):
    """Official ministry notices when Eum's nationwide index is unreachable."""
    response = get(MOLIT_LIST, params={'search_regdate_s': begin.isoformat(), 'search_regdate_e': now.date().isoformat()},
                   headers={'User-Agent': USER_AGENT}, timeout=25)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('MOLIT public list did not return HTTP 200')
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, 'html.parser')
    expected = ['번호', '제목', '등록일자', '담당부서', '조회']
    tables = [table for table in soup.find_all('table')
              if [_clean(cell.get_text(' ', strip=True)) for cell in table.select('thead th')] == expected]
    if len(tables) != 1:
        raise ValueError('MOLIT notice table schema changed')
    rows = tables[0].select('tbody tr')
    parsed = [_molit_notice(row, begin, now.date()) for row in rows]
    parsed = [item for item in parsed if item]
    empty = (len(rows) == 1 and len(rows[0].find_all('td', recursive=False)) == 1
             and _clean(rows[0].get_text(' ', strip=True)) in ('등록된 게시물이 없습니다.', '검색된 결과가 없습니다.'))
    if not parsed and not empty:
        raise ValueError('No verifiable dated MOLIT notices in response')
    notices, seen = [], set()
    for identity, published, title, department in sorted(parsed, key=lambda item: item[1], reverse=True):
        if identity in seen or not LAND_TOPIC.search(title) or not re.search(r'계획|결정|승인|지정|변경|고시|공람', title):
            continue
        seen.add(identity)
        notices.append('\n'.join([title, f'게시일 {published:%Y-%m-%d} · 국토교통부 {department}', MOLIT_DETAIL + identity]))
        if len(notices) >= 5:
            break
    return '\n\n'.join([
        '토지이음 목록에 연결하지 못해 국토교통부 공식 공지사항에서 확인했습니다.',
        f'게시일 기준: {begin:%Y-%m-%d} ~ {now:%Y-%m-%d} · 국토교통부 목록 첫 페이지 · 관련 공지 최대 5건',
        '조회 범위는 국토교통부가 게시한 토지·도시계획·주택사업 관련 공지입니다. 지자체 전체 고시 목록이 아니며, 게시일은 고시일·시행일과 다를 수 있습니다.',
        *(notices or ['현재 조회한 국토교통부 목록에 해당 주제 공지가 없습니다.']),
        '개별 필지의 적용 규제나 건축 가능 여부를 판정한 결과가 아닙니다. 정확한 주소와 고시 원문·도면·담당 지자체에서 적용 내용을 확인하세요.',
        f'수집: {now:%Y-%m-%d %H:%M} (한국시간)',
        '자료: 국토교통부 · 토지·도시계획·주택사업 관련 공식 공지사항\n' + MOLIT_LIST,
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
    try:
        return _molit_updates(get, now, begin)
    except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f'[WARN] MOLIT land-use notices unavailable ({type(exc).__name__}).', file=sys.stderr)
        return '\n\n'.join([
            '최신 공식 토지·도시계획 고시 목록을 확인하지 못했습니다. 이전 고시를 최신 정보로 표시하지 않습니다.',
            explanation,
            f'확인 시도: {now:%Y-%m-%d %H:%M} (한국시간)',
            source + '\n' + MOLIT_LIST,
        ])
