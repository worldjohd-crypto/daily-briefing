"""Deterministic, free historical comparisons; never call a language model."""
import datetime as dt
import json
import re
from pathlib import Path

from free_content import is_naver_article
from history_prices import load_history_prices, compare_price_changes, TABLE_URL, METHOD_URL

CATALOG = Path(__file__).resolve().parents[1] / 'housing-history-articles.json'
TOPICS = {
    'credit': ('대출·금융 규제', ('대출', 'LTV', 'DTI', 'DSR', '가계부채')),
    'rebuild': ('재건축 제도', ('재건축', '초과이익', '부담금')),
    'jeonse': ('전세 수급', ('전세난', '전세 품귀', '전세값', '전셋값', '전세가', '전세 부족')),
    'supply': ('장래 공급·개발', ('공급', '개발', '신도시', '택지', '착공', '입주물량')),
}
EXCLUDE = re.compile(r'부고|모친상|부친상|인사\]|개인전')
ERAS = ('노무현 정부', '문재인 정부')


def current_articles(body):
    """Reuse this build's Naver headlines; do not fetch another news feed."""
    found = []
    for block in body.split('\n\n'):
        lines = block.strip().splitlines()
        if len(lines) >= 2 and is_naver_article(lines[-1]) and not EXCLUDE.search(lines[0]):
            found.append({'title': lines[0], 'url': lines[-1]})
    return found


def select_comparisons(news, articles):
    selected = []
    used_topics = set()
    for item in news:
        for topic, (label, words) in TOPICS.items():
            hits = [word for word in words if word.casefold() in item['title'].casefold()]
            if not hits or topic in used_topics:
                continue
            references = []
            for era in ERAS:
                match = next((a for a in articles if a['era'] == era and topic in a['topics']), None)
                if match:
                    references.append(match)
            if references:
                selected.append({'current': item, 'topic': topic, 'label': label,
                                 'hits': hits, 'references': references})
                used_topics.add(topic)
                break
        if len(selected) == 2:
            break
    return selected


def _price_lines(dataset, date):
    result = compare_price_changes(dataset, date, region='서울')
    lines = []
    if result.get('base_index') is None:
        return ['가격 비교: 공식 월간 지수를 확인하지 못했습니다. 수치를 추정하지 않습니다.']
    month_label = lambda month: month[:4] + '-' + month[4:]
    lines.append(f"서울 아파트 매매가격지수 · 기사월 {month_label(result['base_month'])} = {result['base_index']:.4f}")
    for months, label in ((6, '6개월'), (12, '1년'), (24, '2년')):
        horizon = result['horizons'].get(months, result['horizons'].get(str(months), {}))
        if horizon.get('status') == 'available':
            lines.append(f"이후 {label} ({month_label(horizon['month'])}): {horizon['change_pct']:+.2f}% · 지수 {horizon['index']:.4f}")
        else:
            lines.append(f"이후 {label}: 공식 해당 월 자료 미확인")
    return lines


def build_history_comparison(current_news, get, now, dataset=None, catalog_path=CATALOG):
    catalog = json.loads(Path(catalog_path).read_text(encoding='utf-8'))
    selected = select_comparisons(current_articles(current_news), catalog['articles'])
    weekday = ('월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일')[now.weekday()]
    blocks = [f"🕰️ [과거 상승기 뉴스·집값 비교]\n{now.year % 100}년 {now.month}월 {now.day}일 {weekday}",
              '현재 기사 제목의 주제어와 검증된 과거 기사 표본을 연결한 참고 비교입니다.']
    if not selected:
        blocks.append('오늘 부동산 뉴스에서 검증된 과거 표본과 연결할 주제를 찾지 못했습니다. 억지로 유사 기사를 연결하지 않습니다. 아래는 오늘 기사와의 유사 사례가 아닌 과거 공급대책 참고 자료입니다.')
        selected = [{'current': None, 'topic': 'supply', 'label': '공급대책 참고 사례', 'hits': [],
                     'references': [a for a in catalog['articles'] if 'supply' in a['topics']]}]
    if dataset is None:
        dataset = load_history_prices(get=get, now=now)
    blocks.append('통계 범위: 한국부동산원 월간 아파트 매매가격지수 · 서울\n공식 지수 기준: '
                  + dataset.get('basis', '확인 실패'))
    for group in selected:
        blocks.append('비교 주제: ' + group['label'])
        if group['current']:
            blocks.append('현재 기사: ' + group['current']['title'] + '\n' + group['current']['url'])
            blocks.append('유사점: 현재 제목의 ‘' + '·'.join(group['hits'])
                          + '’와 과거 보도의 ‘' + group['label'] + '’ 주제가 겹칩니다. 조치의 강도·효과까지 같다는 뜻은 아닙니다.')
        for article in group['references']:
            blocks.append(f"과거 기사 · {article['era']} · {article['date']}\n"
                          + article['title'] + '\n' + article['url'])
            blocks.append('\n'.join(_price_lines(dataset, article['date'])))
            blocks.append('당시 내용: ' + article['fact'] + '\n차이점: ' + article['difference'])
        missing = [era for era in ERAS if not any(a['era'] == era for a in group['references'])]
        if missing:
            blocks.append('표본 범위: 이 주제와 연결할 ' + '·'.join(missing) + '의 검증된 기사가 아직 없어 다른 주제 기사로 대체하지 않았습니다.')
    blocks.append('읽는 법: 기사 발행월의 월간 지수와 6·12·24개월 뒤 지수를 비교한 누적 명목 변화율입니다. 기사 당일 시세나 개별 아파트 실거래 수익률이 아닙니다. 기사월 지수에는 발행일 뒤의 같은 달 조사도 포함될 수 있습니다.')
    blocks.append('계산: (비교월 지수 ÷ 기사월 지수 − 1) × 100. 같은 표·같은 지역의 지수만 사용하며 반올림 전 값으로 계산합니다. 서울 지수로 다른 지역의 변화를 설명하지 않습니다.')
    blocks.append('해석 주의: 상승기 기사 표본만으로 전체 시장을 대표할 수 없습니다. 하락·정체도 그대로 표시하며, 2년 뒤가 다음 정부 기간에 속할 수도 있습니다. 과거의 뒤따른 변화는 해당 기사·정책의 효과를 분리한 결과도, 현재의 상승 예측도 아닙니다. 금리·대출·공급 조건의 동일 여부는 별도 확인이 필요합니다.')
    note = dataset.get('note', '')
    collected = '확인 실패'
    if dataset.get('downloadedAt'):
        try:
            stamp = dt.datetime.fromisoformat(dataset['downloadedAt'].replace('Z', '+00:00'))
            if stamp.tzinfo is not None:
                collected = stamp.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M') + ' (한국시간)'
        except ValueError:
            pass
    blocks.append(f"매칭 수집: {now:%Y-%m-%d %H:%M} (한국시간)\n과거 기사 표본 검토: {catalog['reviewed']}\n통계 수집: {collected}" + ('\n통계 상태: ' + note if note else ''))
    blocks.append('자료: 한국부동산원 R-ONE 전국주택가격동향조사 월간 아파트 매매가격지수 · 서울 (무료 공식 API)\n' + TABLE_URL + '\n' + METHOD_URL)
    blocks.append('출처: 과거 기사 표본은 이데일리·동아일보·SBS의 무료 당시 보도입니다. 현재 뉴스는 네이버 원래 주소를 유지합니다. 검증된 표본을 자동 매칭하며 과거 기사 표본 자체는 편집 검토 후 확장합니다. 기사 전체의 의미가 같다는 판정은 아닙니다.')
    return '\n\n'.join(blocks)
