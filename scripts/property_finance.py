"""Current HF mortgage terms and published rates from free official HTML.

No account, API key, paid service or generated commentary is used. Each build
fetches the official pages again. A stale month or changed schema fails visibly
instead of presenting an old or hard-coded rate as current.
"""

import datetime as dt
import re
import sys

import requests
from bs4 import BeautifulSoup


KST = dt.timezone(dt.timedelta(hours=9))
HF_HOMELOAN = 'https://www.hf.go.kr/ko/sub01/sub01_01_01.do'
HF_HOMELOAN_RATES = 'https://www.hf.go.kr/ko/sub01/sub01_01_04.do'
HF_DIDIMDOL = 'https://www.hf.go.kr/ko/sub01/sub01_02_01.do'
HF_DIDIMDOL_RATES = 'https://www.hf.go.kr/ko/sub01/sub01_02_03.do'
USER_AGENT = 'DailyBriefing/1.0 (https://worldjohd-crypto.github.io/daily-briefing/) requests'
SOURCES = {
    'housingfinance': {
        'provider': '한국주택금융공사',
        'sources': [HF_HOMELOAN, HF_HOMELOAN_RATES, HF_DIDIMDOL, HF_DIDIMDOL_RATES],
        'cost': 'free_public_information', 'keyRequired': False,
        'collection': 'live_official_product_summary_and_monthly_rate_html',
        'scope': 'general product summary; published current KST month rates before individual discounts',
        'failurePolicy': 'show unavailable portion; never substitute stale or hard-coded terms/rates',
    },
}


def _text(node):
    return re.sub(r'\s+', ' ', node.get_text(' ', strip=True)).strip()


def _page(get, url, expected_title):
    response = get(url, headers={'User-Agent': USER_AGENT}, timeout=25)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('HF page did not return HTTP 200')
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.find('title')
    if title is None or expected_title not in _text(title) or '한국주택금융공사' not in _text(title):
        raise ValueError('Unexpected HF document')
    return soup


def _product(soup, didimdol=False):
    """Use the official summary and keep exceptions attached to their limits."""
    top = soup.select_one('#cms-content .product_top')
    if top is None:
        raise ValueError('Missing HF product summary')
    groups = {}
    for block in top.find_all('dl', recursive=False):
        label, detail = block.find('dt', recursive=False), block.find('dd', recursive=False)
        if label is None or detail is None or _text(label) in groups:
            raise ValueError('Invalid HF summary group')
        items = [_text(item) for item in detail.select('ul.list_ul03 > li')]
        if not items or any(not text or len(text) > 700 for text in items):
            raise ValueError('Invalid HF summary item')
        groups[_text(label)] = items
    if set(groups) != {'신청대상', '대출요건', '상품구조'}:
        raise ValueError('HF summary schema changed')
    terms, structure = groups['대출요건'], groups['상품구조']
    term_text = ' '.join(terms)
    if not all(keyword in term_text for keyword in ('주택', '소득', 'LTV', 'DTI')):
        raise ValueError('Missing required product conditions')
    if not any('대출한도' in value for value in structure) or not any('대출만기' in value for value in structure):
        raise ValueError('Missing loan amount or maturity')
    # Keep a following parenthesized exception (e.g. first-time/newlywed limit).
    selected_structure = []
    for item in structure:
        if item.startswith(('대출한도', '대출만기')) or item.startswith('('):
            selected_structure.append(item)
    lines = ['조건: ' + ' / '.join(terms), '한도·기간: ' + ' / '.join(selected_structure)]
    if didimdol:
        assets = [value for value in groups['신청대상'] if '순자산' in value]
        if len(assets) != 1:
            raise ValueError('Missing Didimdol asset criterion')
        lines.append('자산: ' + assets[0])
    return '\n'.join(lines)


def _rates(soup, now, didimdol=False):
    board = soup.select_one('.board-area')
    if board is None:
        raise ValueError('Missing HF rate board')
    month = board.select_one('input.b-dt')
    expected_month = now.strftime('%Y-%m')
    if month is None or month.get('value') != expected_month:
        raise ValueError('Current-month HF rate is not available')
    heading = board.select_one('.conTsearch span')
    ym = re.search(r'(\d{4})년\s*(\d{1,2})월', _text(heading) if heading else '')
    if ym is None or (int(ym[1]), int(ym[2])) != (now.year, now.month):
        raise ValueError('HF displayed rate month disagrees')
    published = re.search(r'공시일\s*:\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', _text(board))
    if published is None:
        raise ValueError('Missing HF publication date')
    date = dt.date(*map(int, published.groups()))
    if date > now.date() or date.strftime('%Y-%m') != expected_month:
        raise ValueError('Invalid HF publication date')
    table = board.find('table')
    if table is None:
        raise ValueError('Missing HF rate table')
    terms = ['10년', '15년', '20년', '30년'] + ([] if didimdol else ['40년', '50년'])
    headers = [_text(th) for th in table.select('thead th')]
    if didimdol:
        if headers[-4:] != terms or not headers or '소득수준' not in headers[0]:
            raise ValueError('Didimdol rate columns changed')
    elif headers != ['상품별/만기'] + terms:
        raise ValueError('Bogeumjari rate columns changed')
    rows = []
    for row in table.select('tbody tr'):
        cells = [_text(cell) for cell in row.find_all(['td', 'th'], recursive=False)]
        if len(cells) != len(terms) + 1 or not cells[0] or len(cells[0]) > 100:
            raise ValueError('Invalid HF rate row')
        values = cells[1:]
        if any(not re.fullmatch(r'\d{1,2}\.\d{1,2}', value) or not 0 < float(value) < 30 for value in values):
            raise ValueError('Invalid HF numeric rate')
        rows.append((cells[0], values))
    if didimdol:
        if len(rows) != 4 or any('백만원' not in label for label, _ in rows) or len({label for label, _ in rows}) != 4:
            raise ValueError('Didimdol income tiers changed')
    elif [label for label, _ in rows] != ['u-보금자리론', '아낌e보금자리론', 't-보금자리론']:
        raise ValueError('Bogeumjari product rows changed')
    lines = [f'금리 기준: {now:%Y년 %m월} · 공시일 {date:%Y-%m-%d} · 연 % · 우대 적용 전',
             '만기 순서: ' + ' / '.join(terms)]
    lines += [label + ': ' + ' / '.join(values) for label, values in rows]
    return '\n'.join(lines)


def build_housing_finance(get, now):
    """Fetch both policy mortgages; isolate a failed page without inventing facts."""
    now = (now.replace(tzinfo=KST) if now.tzinfo is None else now).astimezone(KST)
    sections = []
    for name, product_url, rate_url, didimdol in (
        ('보금자리론 (일반)', HF_HOMELOAN, HF_HOMELOAN_RATES, False),
        ('디딤돌대출 (일반)', HF_DIDIMDOL, HF_DIDIMDOL_RATES, True),
    ):
        lines = [name]
        for label, url, parser in (
            ('상품 조건', product_url, lambda soup: _product(soup, didimdol)),
            ('이번 달 공시금리', rate_url, lambda soup: _rates(soup, now, didimdol)),
        ):
            try:
                lines.append(parser(_page(get, url, '디딤돌대출' if didimdol else '보금자리론')))
            except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
                print(f'[WARN] HF {label} unavailable ({type(exc).__name__}).', file=sys.stderr)
                lines.append(label + ': 최신 공식 내용을 확인하지 못했습니다. 아래 공식 원문에서 확인하세요.')
        sections.append('\n'.join(lines))
    return '\n\n'.join([
        '공식 홈페이지에서 매번 새로 확인하는 일반 상품 요약입니다. 개인별 승인·적용 금리는 소득, 주택, 지역, 우대요건 등에 따라 달라집니다.',
        *sections,
        '금리표는 만기 순서대로 읽으세요. 디딤돌 표는 일반 소득구간 기준이며 생애최초 신혼가구 별도표·지방 주택 차감·우대조건은 공식 원문에서 확인하세요.',
        f'수집: {now:%Y-%m-%d %H:%M} (한국시간)',
        '자료: 한국주택금융공사 · 보금자리론 / 디딤돌대출 상품소개·금리안내\n'
        + '\n'.join([HF_HOMELOAN, HF_HOMELOAN_RATES, HF_DIDIMDOL, HF_DIDIMDOL_RATES]),
    ])
