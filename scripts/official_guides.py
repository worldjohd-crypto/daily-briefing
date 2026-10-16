"""Small editorial guides grounded in free, directly checked official pages.

These are evergreen checklists, not newly reported daily news. A deterministic
daily rotation selects an independently written guide; no upstream briefing,
credentials, paid APIs or model calls are used. An unavailable or changed
source causes the corresponding guide to be omitted, never fabricated.
"""

from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup


EDITORIAL_REVIEWED_ON = '2026-10-17'


@dataclass(frozen=True)
class OfficialSource:
    title: str
    url: str
    published: str


SOURCES = {
    'contract': OfficialSource(
        '국토교통부·한국부동산원 부동산거래 전자계약시스템 FAQ',
        'https://irts.molit.go.kr/usr/brd/bscbrd/faq/RtecsFaqList.do',
        '상시 FAQ · 게시일 미표시',
    ),
    'neighborhood': OfficialSource(
        '법제처 찾기쉬운 생활법령정보 · 이사할 곳 주변 조사하기',
        'https://www.easylaw.go.kr/CSP/CnpClsMain.laf?popMenu=ov&csmSeq=666&ccfNo=1&cciNo=1&cnpClsNo=3',
        '상시 안내 · 게시일 미표시',
    ),
    'advertising': OfficialSource(
        '국가법령정보센터 · 공인중개사법 시행령 제17조의2',
        'https://www.law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=106742',
        '시행 2026-08-28 · 원문에서 현행 여부 확인',
    ),
    'saving': OfficialSource(
        '금융위원회 · 특판 예·적금 가입할 때 우대금리 조건 꼼꼼히 확인하세요!',
        'https://www.fsc.go.kr/no040101/?cnId=1646&curPage=248&pastPage=248&srchKey=&srchText=',
        '2023-04-05',
    ),
    'maintenance': OfficialSource(
        '국토교통부·한국부동산원 공동주택관리정보시스템 K-apt',
        'https://www.k-apt.go.kr/web/main/index.do',
        '상시 서비스 안내 · 게시일 미표시',
    ),
    'moving': OfficialSource(
        '법제처 찾기쉬운 생활법령정보 · 이사업체 정하기',
        'https://www.easylaw.go.kr/CSP/CnpClsMain.laf?popMenu=ov&csmSeq=666&ccfNo=3&cciNo=2&cnpClsNo=1',
        '상시 안내 · 게시일 미표시',
    ),
}


# Each variant includes source-specific evidence tokens. If those disappear,
# even a successful HTTP response is insufficient to publish the old guide.
GUIDES = {
    'daily19': ('contract', (
        (
            ('전자계약서', 'PDF', '다운로드'),
            'OX: 전자계약서는 별도 파일로 보관할 수 없다?\n'
            '정답: X\n'
            '국토교통부 FAQ는 전자계약서의 PDF 다운로드와 출력을 안내합니다. '
            '필요한 계약서는 공식 시스템에서 확인하고 보관 방법을 살펴보세요. '
            '모바일 직접 출력에는 제약이 있을 수 있어 PC 이용 안내도 함께 확인하세요.',
        ),
        (
            ('태블릿', '스마트', '가능'),
            'OX: 부동산 전자계약에는 태블릿 PC가 반드시 필요하다?\n'
            '정답: X\n'
            '국토교통부 FAQ는 앱을 설치할 수 있는 본인 명의 스마트폰을 이용하는 방법을 안내합니다. '
            '실제 거래 전에는 지원 기기와 본인확인 절차를 공식 이용 안내에서 확인하세요.',
        ),
    )),
    'daily20': ('neighborhood', (
        (
            ('대중교통', '시장', '마트'),
            '오늘의 확인: 집 밖 생활 동선\n'
            '집을 둘러본 뒤 대중교통 정류장, 장보는 곳, 공원까지의 동선을 확인해 보세요. '
            '공식 이사 안내도 집 주변의 교통과 생활시설을 함께 조사하도록 안내합니다.',
        ),
        (
            ('학교알리미', '학교', '통학'),
            '오늘의 확인: 학교까지 가는 길\n'
            '자녀가 있다면 주변 학교 정보와 실제 통학 동선을 함께 살펴보세요. '
            '공식 이사 안내는 학교알리미 등에서 학교 현황을 확인하는 방법을 제공합니다. '
            '거리뿐 아니라 길을 건너는 지점도 현장에서 확인해 보세요.',
        ),
    )),
    'daily21': ('advertising', (
        (
            ('면적', '입주가능일', '관리비'),
            '광고 읽기: 가격 옆의 조건도 함께\n'
            '매물 비교 메모에 면적, 입주가능일, 주차대수, 관리비를 따로 적어 보세요. '
            '이 항목들은 공식 표시·광고 규정에서 확인할 수 있습니다. '
            '광고에 없는 내용이나 표현이 모호한 부분은 중개사에게 구체적으로 질문하세요.',
        ),
        (
            ('등록번호', '성명', '연락처'),
            '광고 읽기: 누가 올린 매물인지 확인\n'
            '매물 내용과 함께 중개사무소 명칭·연락처·등록번호와 개업공인중개사 이름을 확인해 보세요. '
            '공식 규정의 표시 항목을 질문 목록으로 삼으면 광고를 비교할 때 빠뜨리는 내용을 줄일 수 있습니다.',
        ),
    )),
    'daily22': ('saving', (
        (
            ('우대금리', '최고금리', '조건'),
            '착각: 최고금리 숫자가 곧 내가 받을 금리다\n'
            '금융위원회는 우대금리 조건을 충족하지 못하면 광고의 최고금리보다 낮은 금리가 적용될 수 있다고 안내합니다. '
            '상품설명서에서 기본금리와 우대조건을 나눠 확인하고, 내가 충족할 수 있는 조건을 표시해 보세요.',
        ),
        (
            ('우대금리', '설명', '불확실'),
            '착각: 우대조건은 일단 가입한 뒤 알아봐도 된다\n'
            '금융위원회는 복잡한 조건에 대해 설명을 요청하고, 이벤트에 따라 달라지는 우대금리의 불확실성을 확인하도록 안내합니다. '
            '조건을 이해한 뒤 실제 예상금리를 비교해 보세요.',
        ),
    )),
    'daily23': ('maintenance', (
        (
            ('유지관리이력', '공용부분', '시설교체'),
            '오늘의 확인: 공동시설 수리 이력\n'
            'K-apt의 유지관리이력 메뉴는 공용부분의 시설교체·유지보수 정보를 안내합니다. '
            '궁금한 공사가 있다면 공개된 기록을 먼저 살펴보고 관리사무소에 확인할 질문을 적어 보세요. '
            '기록만으로 현재 시설 상태를 단정하지 마세요.',
        ),
        (
            ('관리비', '세부항목', '공개'),
            '오늘의 확인: 관리비 항목 나눠 보기\n'
            'K-apt는 단지 관리비 조회와 다른 단지 비교, 항목별 통계를 제공합니다. '
            '고지서에서 궁금한 항목을 하나 고른 뒤 같은 발생월 자료인지 확인하며 살펴보세요. '
            '공개 자료가 없는 경우 관리사무소에 해당 내역을 문의하세요.',
        ),
    )),
    'daily24': ('moving', (
        (
            ('사다리', '추가비용', '작업인원'),
            '오늘의 준비: 이사 견적의 포함 범위\n'
            '견적을 비교할 때 작업일시·인원과 사다리차 등 장비, 에어컨 탈부착 비용을 따로 확인해 보세요. '
            '공식 이사 안내는 작업조건과 추가비용 여부를 계약 전에 확인하도록 안내합니다.',
        ),
        (
            ('귀중품', '고가품', '분해'),
            '오늘의 준비: 따로 취급할 짐 목록\n'
            '귀중품과 파손이 걱정되는 물건, 분해·조립이 필요한 가구를 목록으로 적어 보세요. '
            '공식 이사 안내에서 설명하는 취급 방법과 비용 항목을 바탕으로 업체에 확인할 질문을 준비하세요.',
        ),
    )),
}


def _official_text(get, source):
    response = get(source.url, timeout=20)
    response.raise_for_status()
    final = urlparse(getattr(response, 'url', source.url) or source.url)
    expected = urlparse(source.url)
    if final.scheme != 'https' or final.hostname != expected.hostname:
        raise ValueError('Official guide redirected outside the verified source')
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, 'html.parser')
    for node in soup(['script', 'style', 'noscript']):
        node.decompose()
    image_text = ' '.join(image.get('alt', '') for image in soup.find_all('img'))
    return ' '.join((soup.get_text(' ', strip=True) + ' ' + image_text).split())


def build_guides(get, now):
    """Return only directly verified sections; callers must not reuse old text.

    One request is made per distinct official source per invocation. Daily
    rotation is based on the supplied date, so repeated hourly updates on the
    same day preserve the chosen guide. Source fetches only verify availability
    and supporting topics; this is deliberately labelled editorial guidance.
    """
    texts = {}
    for key, source in SOURCES.items():
        try:
            texts[key] = _official_text(get, source)
        except Exception as exc:
            print(f'[WARN] Official guide source unavailable: {key} ({type(exc).__name__})')

    result = {}
    date = now.strftime('%Y-%m-%d')
    day = now.toordinal()
    for section_id, (key, variants) in GUIDES.items():
        markers, body = variants[day % len(variants)]
        text = texts.get(key, '')
        if not text or not all(marker in text for marker in markers):
            continue
        source = SOURCES[key]
        result[section_id] = (
            f'상시 생활 가이드 · 날짜별 순환 ({date})\n\n{body}\n\n'
            f'공식 출처: {source.title}\n{source.url}\n'
            f'원자료: {source.published}\n'
            f'편집 검토: {EDITORIAL_REVIEWED_ON} · 공식 본문 연결 확인: {date}'
        )
    return result
