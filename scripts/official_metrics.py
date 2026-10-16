"""Direct, keyless official metrics. No AI or third-party briefing feeds.

Alternative.me requires attribution beside its Fear & Greed Index.
Wikimedia's aggregate pageview data is CC0 and its API requires a User-Agent.
Each collector accepts a requests-compatible GET callable for offline checks.
"""

import datetime as dt
from urllib.parse import quote

import requests


UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))
FNG_SOURCE = 'https://alternative.me/crypto/fear-and-greed-index/'
FNG_API = 'https://api.alternative.me/fng/'
WIKI_SOURCE = 'https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html'
WIKI_API = 'https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article'
USER_AGENT = 'DailyBriefing/1.0 (https://worldjohd-crypto.github.io/daily-briefing/) requests'
WIKI_TOPICS = (
    ('비트코인', 'Bitcoin'),
    ('인플레이션', 'Inflation'),
    ('금리', 'Interest_rate'),
    ('환율', 'Exchange_rate'),
    ('부동산', 'Real_estate'),
    ('주식시장', 'Stock_market'),
    ('금', 'Gold'),
    ('경제', 'Economy'),
)
CLASSIFICATIONS = {
    'Extreme Fear': '극도의 공포',
    'Fear': '공포',
    'Neutral': '중립',
    'Greed': '탐욕',
    'Extreme Greed': '극도의 탐욕',
}


def _aware(now):
    return now.replace(tzinfo=KST) if now.tzinfo is None else now


def _json(get, url, **kwargs):
    response = get(url, headers={'User-Agent': USER_AGENT}, timeout=25, **kwargs)
    response.raise_for_status()
    return response.json()


def _integer(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a metric')
    number = int(value)
    if str(number) != str(value):
        raise ValueError('Metric must be an integer')
    return number


def build_cryptofear(get, now):
    """Return validated Alternative.me Bitcoin sentiment with adjacent attribution."""
    now = _aware(now).astimezone(UTC)
    source = '자료: Alternative.me (무료 공식 API)\n' + FNG_SOURCE
    try:
        payload = _json(get, FNG_API, params={'limit': 8})
        if payload.get('metadata', {}).get('error'):
            raise ValueError('Provider reported an error')
        rows = []
        seen = set()
        for raw in payload['data']:
            value = _integer(raw['value'])
            stamp = _integer(raw['timestamp'])
            date = dt.datetime.fromtimestamp(stamp, UTC).date()
            classification = raw['value_classification']
            if not 0 <= value <= 100 or classification not in CLASSIFICATIONS or date in seen:
                raise ValueError('Invalid index data')
            seen.add(date)
            rows.append((date, value, classification))
        rows.sort(reverse=True)
        if not rows or not dt.timedelta(0) <= now.date() - rows[0][0] <= dt.timedelta(days=3):
            raise ValueError('Latest index is missing or outdated')
        latest_date, latest_value, latest_class = rows[0]
        lines = [f'공식 지수 기준일: {latest_date:%Y년 %m월 %d일} (UTC)',
                 f'비트코인 공포·탐욕 지수: {latest_value}/100 · {CLASSIFICATIONS[latest_class]}',
                 source]
        by_date = {date: value for date, value, _ in rows}
        previous = by_date.get(latest_date - dt.timedelta(days=1))
        if previous is not None:
            lines.append(f'전일: {previous}/100 · 전일 대비 {latest_value - previous:+d}포인트')
        week_ago = by_date.get(latest_date - dt.timedelta(days=7))
        if week_ago is not None:
            lines.append(f'7일 전: {week_ago}/100 · 7일 전 대비 {latest_value - week_ago:+d}포인트')
        lines.extend(['0은 극도의 공포, 100은 극도의 탐욕을 뜻합니다.',
                      f'수집: {now.astimezone(KST):%Y-%m-%d %H:%M} (한국시간)'])
        return '\n\n'.join(lines)
    except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError, OSError):
        return '최신 공식 지수를 확인하지 못했습니다. 이전 값을 오늘 값으로 표시하지 않습니다.\n\n' + source


def _daily_views(payload, article, start, end):
    """Reject gaps, duplicate dates, wrong articles/agents and malformed counts."""
    by_date = {}
    for row in payload['items']:
        stamp = row['timestamp']
        if not isinstance(stamp, str) or len(stamp) != 10 or not stamp.endswith('00'):
            raise ValueError('Invalid daily timestamp')
        date = dt.datetime.strptime(stamp, '%Y%m%d%H').date()
        views = _integer(row['views'])
        if (date in by_date or not start <= date <= end or views < 0
                or row.get('project') != 'en.wikipedia'
                or row.get('article') != article
                or row.get('agent') != 'user'
                or row.get('access') != 'all-access'
                or row.get('granularity') != 'daily'):
            raise ValueError('Invalid pageview series')
        by_date[date] = views
    expected = {start + dt.timedelta(days=offset) for offset in range(14)}
    if set(by_date) != expected:
        raise ValueError('Incomplete two-week series')
    recent_start = end - dt.timedelta(days=6)
    return (sum(views for date, views in by_date.items() if date >= recent_start),
            sum(views for date, views in by_date.items() if date < recent_start))


def build_wikiinterest(get, now):
    """Compare complete seven-day pageview windows for eight fixed English pages."""
    now = _aware(now).astimezone(UTC)
    # UTC daily data can lag; never mix partial current-day views into a week.
    end = now.date() - dt.timedelta(days=2)
    recent_start = end - dt.timedelta(days=6)
    previous_end = recent_start - dt.timedelta(days=1)
    start = end - dt.timedelta(days=13)
    lines = ['영어 위키백과 고정 8개 경제 문서의 읽기 관심도',
             f'최근 7일: {recent_start:%Y-%m-%d} ~ {end:%Y-%m-%d} (UTC)\n'
             f'직전 7일: {start:%Y-%m-%d} ~ {previous_end:%Y-%m-%d} (UTC)',
             '사용자 조회수 합계입니다. 전체 인기 주제 순위나 투자심리 지수가 아닙니다.']
    results, missing = [], []
    for label, article in WIKI_TOPICS:
        url = (f'{WIKI_API}/en.wikipedia/all-access/user/{quote(article, safe="")}/daily/'
               f'{start:%Y%m%d}00/{end:%Y%m%d}00')
        try:
            current, previous = _daily_views(_json(get, url), article, start, end)
            change = f'{(current / previous - 1) * 100:+.1f}%' if previous else '비교 불가(직전 0회)'
            results.append((current, f'{label}: {current:,}회 · 직전 7일 {previous:,}회 · {change}'))
        except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError, OSError):
            missing.append(label)
    lines.extend(text for _, text in sorted(results, key=lambda item: item[0], reverse=True))
    if missing:
        lines.append('14일 전체 자료 미확인: ' + ', '.join(missing))
    if not results:
        lines.append('공식 조회수 자료를 확인하지 못했습니다. 다음 자동 갱신에서 다시 확인합니다.')
    lines.extend([f'수집: {now.astimezone(KST):%Y-%m-%d %H:%M} (한국시간)',
                  '자료: Wikimedia Analytics API (무료 공식 API, CC0)\n' + WIKI_SOURCE])
    return '\n\n'.join(lines)
