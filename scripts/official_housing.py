"""Official housing/commerce sources; never use another briefing as a feed.

The Seoul public website exposes its displayed summary as JSON. Its normal
anonymous session cookie is kept in memory for this build only. No account,
API key, paid service or stored browser session is used.
"""
import datetime
import math
import re


SEOUL_PAGE = 'https://data.seoul.go.kr/SeoulRtd/map'
SEOUL_SUMMARY = 'https://data.seoul.go.kr/SeoulRtd/api/consumption/hotspot-summary'
SEOUL_AREAS = ('강남역', '건대입구역', '가로수길', '명동 관광특구')
KST = datetime.timezone(datetime.timedelta(hours=9))

SOURCES = {
    'transactions': {
        'provider': '국토교통부',
        'url': 'https://www.data.go.kr/data/15126469/openapi.do',
        'officialPage': 'https://rt.molit.go.kr/pt/xls/xls.do',
        'cost': 'free',
        'authentication': 'public_data_service_key_required',
    },
    'seoulcommerce': {
        'provider': '서울특별시 서울 실시간 도시데이터',
        'url': SEOUL_PAGE,
        'endpoint': SEOUL_SUMMARY,
        'documentation': 'https://data.seoul.go.kr/dataVisual/seoul/guide.do',
        'cost': 'free',
        'authentication': 'anonymous_public_website_session_in_memory_only',
        'scope': list(SEOUL_AREAS),
        'metric': '신한카드 내국인 실시간 소비 기반 상권 현황',
        'sourceUpdateIntervalMinutes': 10,
    },
}

EXCLUSIONS = {
    'transactions': {
        'reason': ('국토교통부 무료 실거래가 API 인증키가 설정되지 않아 제외. '
                   '공개 자료는 계약일 기준이며 전국 당일 신규 등록 거래의 '
                   '완전한 집계를 제공한다고 볼 수 없어 해당 제목으로 대체하지 않음.'),
        'source': SOURCES['transactions']['url'],
        'status': 'omitted_missing_free_api_key_and_unverified_registration_scope',
        'paid': False,
    },
}


def build_transactions(get, now):
    """Omit unsupported nationwide newly registered transactions, without I/O."""
    return None


def _local_time(now):
    return now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)


def _summary(payload, now):
    """Reject unavailable, stale or malformed values rather than label them live."""
    if not isinstance(payload, dict):
        return None
    stamp = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', str(payload.get('dateTime', '')))
    if stamp is None:
        return None
    measured = datetime.datetime.strptime(stamp.group(), '%Y-%m-%d %H:%M').replace(tzinfo=KST)
    age = now - measured
    if age < -datetime.timedelta(minutes=20) or age > datetime.timedelta(hours=2):
        return None
    status = payload.get('cmrclText')
    if status not in {'한산한', '보통', '바쁜', '분주한'}:
        return None
    rates = []
    for key in ('hourChangeRate', 'weekChangeRate'):
        value = payload.get(key)
        if value is None:
            rates.append('정보 없음')
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            rates.append(f'{value:+.1f}%')
        else:
            return None
    return stamp.group(), status, rates


def build_seoulcommerce(get, now):
    """Read the same free official summaries shown for four selected hotspots.

    ``get`` is injected for normal request timeouts, testability and auditing.
    A plain requests.get can be used: cookies returned by the public map page
    are passed only to this provider's summary endpoint and never persisted.
    On a failed source, return None so callers can omit this optional section.
    """
    now = _local_time(now)
    try:
        page = get(SEOUL_PAGE, timeout=20)
        page.raise_for_status()
        cookies = page.cookies
    except Exception:
        return None

    rows = []
    for area in SEOUL_AREAS:
        try:
            response = get(
                SEOUL_SUMMARY, params={'hotspotNm': area}, cookies=cookies,
                headers={'Referer': SEOUL_PAGE, 'Content-Type': 'application/json'},
                timeout=20,
            )
            response.raise_for_status()
            checked = _summary(response.json(), now)
            if checked is None:
                continue
            stamp, status, rates = checked
            rows.append(f'{area}: {status}\n'
                        f'1시간 전 대비 {rates[0]} · 지난주 동시간 대비 {rates[1]}\n'
                        f'자료 기준: {stamp}')
        except Exception:
            continue
    if not rows:
        return None

    return (f'{now:%Y-%m-%d} 서울 주요 상권\n'
            f'선택 상권 {len(rows)}곳 · 신한카드 내국인 소비 기준\n\n'
            + '\n\n'.join(rows)
            + f'\n\n확인: {now:%Y-%m-%d %H:%M} (한국시간)'
            + '\n상권 현황은 카드 소비 데이터를 가공한 지표로 실제 현장과 차이가 있을 수 있습니다.'
            + '\n출처: 서울특별시 서울 실시간 도시데이터\n' + SEOUL_PAGE)
