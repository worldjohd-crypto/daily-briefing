"""Free, direct apartment-sale cases from MOLIT's public CSV interface.

These are individual contract-date records, not a national registration-day
statistic. The anonymous cookie from the ordinary download page is held only
in memory and sent only to the same official host. There is one CSV download
per build; never retry a quota or access failure by obtaining another session.
"""
import csv
import datetime
import io
from decimal import Decimal, InvalidOperation


KST = datetime.timezone(datetime.timedelta(hours=9))
PAGE = 'https://rt.molit.go.kr/pt/xls/xls.do'
CHECK = 'https://rt.molit.go.kr/pt/xls/ptXlsDownDataCheck.do'
CSV = 'https://rt.molit.go.kr/pt/xls/ptXlsCSVDown.do'
CASE_LIMIT = 12
SOURCES = {
    'transactions': {
        'provider': '국토교통부 실거래가 공개시스템',
        'url': PAGE,
        'endpoint': CSV,
        'documentation': 'https://www.data.go.kr/data/3050988/fileData.do',
        'cost': 'free',
        'authentication': 'anonymous_public_website_session_in_memory_only',
        'scope': '전국 아파트 매매, 최근 7일 계약, 해제 신고 제외, 최대 12개 사례',
        'dateBasis': 'contract_date_not_registration_date',
        'downloadsPerBuild': 1,
    },
}


def _parameters(start, end):
    data = {key: '' for key in (
        'srhNewRonSecd', 'srhSidoCd', 'srhSggCd', 'srhEmdCd', 'srhRoadNm',
        'srhLoadCd', 'srhHsmpCd', 'srhArea', 'srhFromAmount', 'srhToAmount',
        'srhLrArea', 'mobileAt',
    )}
    data.update({key: '전체' for key in (
        'sidoNm', 'sggNm', 'emdNm', 'loadNm', 'areaNm', 'hsmpNm',
    )})
    data.update(srhThingNo='A', srhDelngSecd='1', srhAddrGbn='1',
                srhLfstsSecd='1', srhFromDt=start.isoformat(),
                srhToDt=end.isoformat())
    return data


def _parse_csv(content, start, end):
    """Strictly parse the official export, excluding rescinded/malformed cases."""
    if not isinstance(content, bytes) or len(content) > 12_000_000:
        raise ValueError('Invalid or oversized official export')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('cp949')
    records = csv.reader(io.StringIO(text))
    required = {'시군구', '단지명', '전용면적(㎡)', '계약년월', '계약일',
                '거래금액(만원)', '층', '해제사유발생일'}
    header = None
    for _ in range(35):
        candidate = next(records, None)
        if candidate is None:
            break
        if required.issubset(candidate):
            header = candidate
            break
    if header is None:
        raise ValueError('No official apartment-sale CSV header')

    rows, seen = [], set()
    for values in records:
        if len(values) != len(header):
            continue
        record = {key: value.strip() for key, value in zip(header, values)}
        if record['해제사유발생일'] not in ('', '-'):
            continue
        try:
            ym = record['계약년월']
            if len(ym) != 6 or not ym.isdigit():
                continue
            contract = datetime.date(int(ym[:4]), int(ym[4:]), int(record['계약일']))
            price = int(record['거래금액(만원)'].replace(',', ''))
            area = Decimal(record['전용면적(㎡)'])
            floor = int(record['층'])
            if not start <= contract <= end or price <= 0 or not area.is_finite() or area <= 0:
                continue
            district, apartment = record['시군구'], record['단지명']
            if not district or not apartment or any('<' in value or '\n' in value
                                                     for value in (district, apartment)):
                continue
        except (ValueError, InvalidOperation):
            continue
        row = (contract, district, apartment, area, price, floor)
        if row not in seen:
            seen.add(row)
            rows.append(row)
    # Stable sorting keeps the source's order for contracts on the same day.
    rows.sort(key=lambda row: row[0], reverse=True)
    return rows


def _source():
    return '\n\n자료: 국토교통부 실거래가 공개시스템 (무료 공개 CSV)\n' + PAGE


def build_transactions(get, now):
    """Read the normal public download interface without a key or paid API."""
    now = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
    end = now.date()
    start = end - datetime.timedelta(days=6)
    heading = (f'{now:%Y-%m-%d} 전국 아파트 매매 실거래가\n'
               f'계약일 조회 범위: {start:%Y-%m-%d} ~ {end:%Y-%m-%d}')
    try:
        page = get(PAGE, timeout=20)
        page.raise_for_status()
        cookies = page.cookies
        kwargs = {'params': _parameters(start, end), 'cookies': cookies,
                  'headers': {'Referer': PAGE}, 'timeout': 45}
        checked = get(CHECK, **kwargs)
        checked.raise_for_status()
        info = checked.json()
        if info.get('error') or not isinstance(info.get('cnt'), int):
            raise ValueError('Official export is not available')
        if info['cnt'] == 0:
            return (heading + '\n\n해당 계약일 범위에서 공개된 아파트 매매 사례가 없습니다.'
                    + '\n신고 지연·해제로 조회 결과가 바뀔 수 있습니다.' + _source())
        response = get(CSV, **kwargs)
        response.raise_for_status()
        rows = _parse_csv(response.content, start, end)
        if not rows:
            raise ValueError('No valid uncancelled apartment cases')
    except Exception:
        return (heading + '\n\n현재 국토부 공개 자료를 확인하지 못했습니다. 다음 자동 갱신에서 다시 확인합니다.'
                + '\n아래 공식 자료제공 페이지에서도 직접 조회할 수 있습니다.' + _source())

    cases = []
    for contract, district, apartment, area, price, floor in rows[:CASE_LIMIT]:
        cases.append(f'{district} · {apartment}\n'
                     f'계약 {contract:%Y-%m-%d} · 전용 {area.normalize():f}㎡ · {floor}층\n'
                     f'매매 {price:,}만원')
    return (heading
            + f'\n이번 조회 파일의 유효 사례 {len(rows):,}건 중 {len(cases)}개 표시'
            + '\n최근 계약일 순 (같은 날은 원자료 순서)\n\n'
            + '\n\n'.join(cases)
            + '\n\n개별 거래 참고용이며 전국 전체 거래 통계·신규 등록 순위가 아닙니다.'
            + '\n계약일 기준으로, 아직 신고되지 않은 거래는 포함되지 않습니다.'
            + '\n해제 신고된 사례는 제외하며 신고·정정·해제로 내용이 달라질 수 있습니다.'
            + f'\n확인: {now:%Y-%m-%d %H:%M} (한국시간)'
            + _source())
