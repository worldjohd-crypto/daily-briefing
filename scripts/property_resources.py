"""Useful housing information from free, direct official public sources.

MyHome's public website search response needs no account or API key. It is a
website endpoint, not an OpenAPI with a promised schema; fail visibly on changes.
Guarantee terms, mortgage rates/terms and land-use notices are also fetched
from their official public pages on every build, without a paid client.
"""

import datetime as dt
import re
import sys
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from property_guarantee import build_rental_guarantee, SOURCES as GUARANTEE_SOURCES
from property_finance import build_housing_finance, SOURCES as FINANCE_SOURCES
from property_land import build_land_updates, SOURCES as LAND_SOURCES


KST = dt.timezone(dt.timedelta(hours=9))
MYHOME_LIST = 'https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcList.do'
MYHOME_SOURCE = 'https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcView.do'
MYHOME_DETAIL = 'https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcDetailView.do?pblancId='
LH_SOURCE = 'https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancList.do?mi=1026'
LH_DETAIL = 'https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do'
# Labels/codes exposed in the official residential rental selector. Daycare,
# commercial rentals, land and sale housing are deliberately outside this card.
LH_HOUSING_TYPES = {
    '통합공공임대': ('06', '48'), '통합공공임대(신혼희망)': ('39', '51'),
    '국민임대': ('06', '07'), '공공임대': ('06', '08'), '영구임대': ('06', '09'),
    '행복주택': ('06', '10'), '행복주택(신혼희망)': ('39', '42'), '장기전세': ('06', '11'),
    '신축다세대매입임대': ('06', '27'), '매입임대': ('13', '26'), '전세임대': ('13', '17'),
    '집주인임대': ('13', '36'), '6년 공공임대주택': ('06', '52'),
}
USER_AGENT = 'DailyBriefing/1.0 (https://worldjohd-crypto.github.io/daily-briefing/) requests'
SOURCES = {
    'publicrental': {
        'provider': '국토교통부·LH 마이홈포털', 'source': MYHOME_SOURCE,
        'endpoint': MYHOME_LIST, 'cost': 'free', 'keyRequired': False,
        'collection': 'public_website_search_json',
        'scope': 'current and previous calendar month, nationwide, active status, first page up to five notices',
        'fallback': {
            'provider': 'LH 청약플러스', 'source': LH_SOURCE,
            'collection': 'public_server_rendered_html', 'cost': 'free', 'keyRequired': False,
            'scope': 'LH rental supply only; current and previous month; active status and nonexpired closing date',
        },
    },
}
SOURCES.update(GUARANTEE_SOURCES)
SOURCES.update(FINANCE_SOURCES)
SOURCES.update(LAND_SOURCES)


def _clean(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('Expected public text')
    return re.sub(r'\s+', ' ', value).strip()


def _announcement(raw, now, begin):
    """Keep only publicly active, dated and identifiable official notices."""
    if not isinstance(raw, dict):
        raise ValueError('Invalid notice')
    if raw.get('prgrStts') not in ('모집중', '정정공고중'):
        return None
    if raw.get('useAt') != 'Y':
        return None
    identity = str(raw['pblancId'])
    stamp = raw['rcritPblancDe']
    if not re.fullmatch(r'\d{1,12}', identity) or not isinstance(stamp, str) or not re.fullmatch(r'\d{8}', stamp):
        raise ValueError('Invalid notice identity or date')
    date = dt.datetime.strptime(stamp, '%Y%m%d').date()
    if date > now.date() or date < begin:
        return None
    title = _clean(raw['pblancNm'])
    agency = _clean(raw['suplyInsttNm'])
    if not title or not agency:
        raise ValueError('Missing notice title or agency')
    region = (_clean(raw.get('brtcCodeNm')) or _clean(raw.get('mhshldBrtcCodeNm'))
              or _clean(raw.get('lfstsAreaBrtcCodeNm')))
    corrected = raw.get('sttusSe') == '2' or raw.get('prgrStts') == '정정공고중'
    caption = ('[정정공고] ' if corrected else '') + title
    facts = f'공고일 {date:%Y-%m-%d} · {agency}' + (f' · {region}' if region else '')
    return identity, caption + '\n' + facts + '\n' + MYHOME_DETAIL + identity


def _lh_announcement(row, now, begin):
    """Parse the public LH table without executing scripts or trusting its href."""
    cells = row.find_all('td', recursive=False)
    if len(cells) != 9:
        return None
    status = cells[7].get_text(' ', strip=True)
    if status not in ('공고중', '정정공고중', '접수중'):
        return None
    anchor = cells[2].select_one('a.wrtancInfoBtn')
    if anchor is None:
        return None
    identity = anchor.get('data-id1', '')
    codes = [anchor.get('data-id' + str(i), '') for i in (2, 3, 4)]
    if not re.fullmatch(r'\d{16}', identity) or any(not re.fullmatch(r'\d{2}', code) for code in codes):
        return None
    stamps = [cell.get_text(strip=True) for cell in cells[5:7]]
    if any(not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}', value) for value in stamps):
        return None
    try:
        published, closing = [dt.datetime.strptime(value, '%Y.%m.%d').date() for value in stamps]
    except ValueError:
        return None
    if not begin <= published <= now.date() <= closing or closing < published:
        return None
    # The relative "1일전" badge belongs to the page UI, not the notice title.
    for badge in anchor.select('em.day'):
        badge.decompose()
    title = _clean(anchor.get_text(' ', strip=True))
    region = _clean(cells[3].get_text(' ', strip=True))
    kind = _clean(cells[1].get_text(' ', strip=True))
    if not title or not region or LH_HOUSING_TYPES.get(kind) != tuple(codes[1:]):
        return None
    params = dict(panId=identity, ccrCnntSysDsCd=codes[0], uppAisTpCd=codes[1], aisTpCd=codes[2], mi='1026')
    facts = (f'공고일 {published:%Y-%m-%d} · 마감일 {closing:%Y-%m-%d}'
             f' · {region} · {kind} · {status}')
    return identity, title + '\n' + facts + '\n' + LH_DETAIL + '?' + urlencode(params)


def _lh_public_rental(get, now, begin):
    """Collect actual LH SSR notices if the MyHome aggregate is unavailable."""
    response = get(LH_SOURCE, headers={'User-Agent': USER_AGENT}, timeout=25)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('LH public list did not return HTTP 200')
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.select_one('.bbs_ListA')
    expected = ['번호', '유형', '공고명', '지역', '첨부', '게시일', '마감일', '상태', '조회수']
    if table is None or [cell.get_text(' ', strip=True) for cell in table.select('thead th')] != expected:
        raise ValueError('LH public table schema changed')
    notices, seen = [], set()
    for row in table.select('tbody tr'):
        item = _lh_announcement(row, now, begin)
        if item and item[0] not in seen:
            seen.add(item[0])
            notices.append(item[1])
        if len(notices) >= 5:
            break
    if not notices:
        raise ValueError('No current dated LH notices could be verified')
    return '\n\n'.join([
        f'조회 범위: {begin:%Y-%m} ~ {now:%Y-%m} 공고 · 전국 · LH 임대주택 공급 공고',
        'LH 공식 목록에서 공고중·정정공고중·접수중이며 마감일이 지나지 않은 공고를 최대 5건 소개합니다. 접수 시작일과 세부 마감시간은 원문에서 확인하세요.',
        *notices,
        '신청 전 공고 원문에서 접수기간·지역·무주택·소득·자산 기준과 정정공고를 확인하세요.',
        f'수집: {now:%Y-%m-%d %H:%M} (한국시간)',
        '자료: LH 한국토지주택공사 · LH 청약플러스 임대주택 공식 공고\n' + LH_SOURCE,
    ])


def build_public_rental(get, now):
    """Collect up to five current official notices, preserving real notice links."""
    now = (now.replace(tzinfo=KST) if now.tzinfo is None else now).astimezone(KST)
    this_month = now.date().replace(day=1)
    begin = (this_month - dt.timedelta(days=1)).replace(day=1)
    source = '자료: 마이홈포털 · 국토교통부·LH 공식 공개 모집공고\n' + MYHOME_SOURCE
    params = {key: '' for key in (
        'srchbrtcCode', 'srchsignguCode', 'searchTyId', 'srchSuplyTy',
        'srchHouseTy', 'srchSuplyPrvuseAr', 'srchBassMtRntchrg', 'lfstsTyAt', 'srchPblancNm')}
    params.update(pageIndex=1, srchPrgrStts='1',
                  srchRcritPblancDeYearMtBegin=begin.strftime('%Y%m'),
                  srchRcritPblancDeYearMtEnd=now.strftime('%Y%m'))
    try:
        response = get(MYHOME_LIST, params=params, headers={'User-Agent': USER_AGENT}, timeout=25)
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('MyHome public list did not return HTTP 200')
        response.encoding = 'utf-8'
        payload = response.json()
        rows = payload['resultList']
        total = payload['resultCnt']
        if not isinstance(rows, list) or type(total) is not int or total < 0:
            raise ValueError('Invalid notice list')
        if total > 0 and not rows:
            raise ValueError('Notice list is incomplete')
        notices, seen = [], set()
        for raw in rows:
            item = _announcement(raw, now, begin)
            if item and item[0] not in seen:
                seen.add(item[0])
                notices.append(item[1])
            if len(notices) >= 5:
                break
        if total > 0 and not notices:
            raise ValueError('No verifiable current notices in the response')
        lines = [f'조회 범위: {begin:%Y-%m} ~ {now:%Y-%m} 공고 · 전국 · 모집중 표시 기준',
                 '공식 목록 순서로 최대 5건을 소개합니다. 모집중 표시는 오늘 접수 가능하다는 뜻과 다를 수 있습니다.']
        lines.extend(notices or ['현재 조회 조건에 해당하는 공식 모집공고가 없습니다.'])
        lines.extend(['신청 전 공고 원문에서 접수기간·지역·무주택·소득·자산 기준과 정정공고를 확인하세요.',
                      f'수집: {now:%Y-%m-%d %H:%M} (한국시간)', source])
        return '\n\n'.join(lines)
    except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f'[WARN] MyHome rental notices unavailable ({type(exc).__name__}); checking official LH HTML.', file=sys.stderr)
    try:
        return _lh_public_rental(get, now, begin)
    except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f'[WARN] LH rental notices unavailable ({type(exc).__name__}).', file=sys.stderr)
        return ('최신 공식 모집공고 목록을 확인하지 못했습니다. 이전 공고를 오늘 모집중인 공고로 표시하지 않습니다.'
                '\n\n공식 모집공고 목록에서 지역과 모집상태를 선택해 확인하세요.\n\n'
                + source + '\n' + LH_SOURCE)


def build_property_resources(get, now):
    """Read each official provider on every build; builders isolate outages."""
    return [
        ('publicrental', '🏘️', '공공임대 모집공고', build_public_rental(get, now)),
        ('rentalguarantee', '🛡️', '전세보증금 지키기', build_rental_guarantee(get, now)),
        ('housingfinance', '🏦', '내집마련 대출 정보', build_housing_finance(get, now)),
        ('landcheck', '🗺️', '토지·규제 최신 고시', build_land_updates(get, now)),
    ]
