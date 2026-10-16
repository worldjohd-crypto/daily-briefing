"""Useful housing information from free, direct official public sources.

MyHome's public website search response needs no account or API key. It is a
website endpoint, not an OpenAPI with a promised schema; fail visibly on changes.
Other cards are dated editorial guides based on the linked official pages, not
live loan quotations, paid services or AI-generated commentary.
"""

import datetime as dt
import re

import requests


KST = dt.timezone(dt.timedelta(hours=9))
MYHOME_LIST = 'https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcList.do'
MYHOME_SOURCE = 'https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcView.do'
MYHOME_DETAIL = 'https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcDetailView.do?pblancId='
HUG_SOURCE = 'https://www.khug.or.kr/hug/web/ig/dr/igdr000001.jsp?tabId=1'
HF_HOMELOAN = 'https://www.hf.go.kr/ko/sub01/sub01_01_01.do'
HF_DIDIMDOL = 'https://www.hf.go.kr/ko/sub01/sub01_02_01.do'
EUM_SOURCE = 'https://www.eum.go.kr/web/am/visit.jsp'
GUIDE_REVIEWED = '2026-10-17'
USER_AGENT = 'DailyBriefing/1.0 (https://worldjohd-crypto.github.io/daily-briefing/) requests'
SOURCES = {
    'publicrental': {
        'provider': '국토교통부·LH 마이홈포털', 'source': MYHOME_SOURCE,
        'endpoint': MYHOME_LIST, 'cost': 'free', 'keyRequired': False,
        'collection': 'public_website_search_json',
        'scope': 'current and previous calendar month, nationwide, active status, first page up to five notices',
    },
    'rentalguarantee': {
        'provider': 'HUG 주택도시보증공사', 'source': HUG_SOURCE,
        'cost': 'free_public_information', 'collection': 'evergreen_guide', 'reviewed': GUIDE_REVIEWED,
    },
    'housingfinance': {
        'provider': '한국주택금융공사', 'sources': [HF_HOMELOAN, HF_DIDIMDOL],
        'cost': 'free_public_information', 'collection': 'evergreen_guide', 'reviewed': GUIDE_REVIEWED,
    },
    'landcheck': {
        'provider': '국토교통부 토지이음', 'source': EUM_SOURCE,
        'cost': 'free_public_information', 'collection': 'evergreen_guide', 'reviewed': GUIDE_REVIEWED,
    },
}


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
    except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError):
        return ('최신 공식 모집공고 목록을 확인하지 못했습니다. 이전 공고를 오늘 모집중인 공고로 표시하지 않습니다.'
                '\n\n마이홈 공식 목록에서 지역과 모집상태를 선택해 확인하세요.\n\n' + source)


def build_property_resources(get, now):
    """Return (id, emoji, title, plain body) cards for the existing renderer."""
    reviewed = f'상시 확인 가이드 · 공식 안내 확인일: {GUIDE_REVIEWED}'
    hug = '\n\n'.join([
        reviewed,
        '전세보증금반환보증은 계약 종료 후 돌려받지 못한 전세보증금의 반환을 보장하는 상품입니다. 계약 전에 가입 가능 여부부터 확인하세요.',
        '① 대상 주택·보증금·신청기한을 확인합니다. 신청 가능한 기간을 넘기지 않도록 계약 일정과 함께 점검하세요.',
        '② 등기부의 소유자와 권리침해, 선순위채권을 확인합니다. 다가구주택은 다른 세입자의 선순위 보증금도 확인 대상입니다.',
        '③ 전입신고·확정일자·실제 거주, 전세계약서 및 건축물대장 관련 요건을 HUG 공식 조건과 대조합니다.',
        '④ 보증 가입 가능 여부와 제출서류를 HUG에서 확인합니다. 전세대출 보증과 보증금 반환보증은 역할이 다릅니다.',
        '이 카드는 무료 공식 정보 안내입니다. 실제 보증 가입에는 보증료가 발생할 수 있습니다.',
        '자료: HUG 주택도시보증공사 · 전세보증금반환보증 상품개요\n' + HUG_SOURCE,
    ])
    loans = '\n\n'.join([
        reviewed,
        '집값만 보기 전에 내 자금과 월 상환액을 함께 계산하세요. HF의 보금자리론·디딤돌대출 공식 안내에서 아래 항목을 비교할 수 있습니다.',
        '① 신청대상: 세대·주택 보유수, 부부합산 소득, 생애최초·신혼 등 구분을 확인합니다. 디딤돌대출은 자산 기준도 확인합니다.',
        '② 대상주택: 주택가격·전용면적·주택 유형을 확인합니다. 내가 보는 매물이 상품의 대상인지 먼저 대조하세요.',
        '③ 자금계획: 대출한도, LTV·DTI, 만기와 상환방식을 확인한 뒤 월 원금+이자에 관리비·세금·수리비를 더해 봅니다.',
        '④ 실행일정: 상품별 신청·심사·실행 절차를 잔금 일정과 맞춥니다. 금리·우대조건·중도상환 조건은 공식 최신 안내로 확인하세요.',
        '여기에 표시한 것은 비교 순서이며 개인별 승인 한도나 적용 금리 계산 결과가 아닙니다.',
        '자료: 한국주택금융공사 · 보금자리론 / 디딤돌대출 상품소개\n' + HF_HOMELOAN + '\n' + HF_DIDIMDOL,
    ])
    land = '\n\n'.join([
        reviewed,
        '같은 동네라도 필지마다 허용 용도와 규제가 다릅니다. 토지·상가·주택을 검토할 때 주소를 정확히 넣고 확인하세요.',
        '① 토지이음에서 지번·도로명으로 대상 필지를 찾고 지목·면적·지역·지구 지정현황을 확인합니다.',
        '② 「행위제한내용설명」에서 건축 가능한 용도, 건폐율·용적률, 층수·높이, 건축선·도로조건을 살펴봅니다.',
        '③ 도시계획과 고시정보에서 관련 결정고시·도면을 확인합니다. 개발계획이 있다는 말과 실제 결정된 고시는 구분해서 봅니다.',
        '④ 계획한 건축·용도변경의 가능 여부는 표시된 근거 법령과 담당 지자체의 허가 요건까지 확인합니다.',
        '자료: 국토교통부 토지이음 · 주요 서비스와 열람 안내\n' + EUM_SOURCE,
    ])
    return [
        ('publicrental', '🏘️', '공공임대 모집공고', build_public_rental(get, now)),
        ('rentalguarantee', '🛡️', '전세보증금 지키기', hug),
        ('housingfinance', '🏦', '내집마련 대출 확인 순서', loans),
        ('landcheck', '🗺️', '계약 전 토지·규제 확인', land),
    ]
