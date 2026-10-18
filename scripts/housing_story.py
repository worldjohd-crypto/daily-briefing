"""Evidence-backed housing stories with an explicitly fictional cash-flow model.

Only local, reviewed official CSV evidence is read.  This module never downloads
another transaction export and never invokes a language model.  Public sale
records describe contracts, not an identified owner's investment history.
"""
import datetime as dt
import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlparse

from housing_history import CATALOG, TOPICS, current_articles, select_comparisons
from official_transactions import PAGE, _parse_csv


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'housing-story-evidence.json'
KST = dt.timezone(dt.timedelta(hours=9))
SCENARIO_LOAN_RATIO = Decimal('0.30')
SCENARIO_ANNUAL_RATE = Decimal('0.045')
SCENARIO_ACQUISITION_RESERVE_RATIO = Decimal('0.04')
SCENARIO_SALE_RESERVE_RATIO = Decimal('0.01')
SCENARIO_MONTHLY_HOLDING_BUDGET = Decimal('15')


class EvidenceError(ValueError):
    """A reviewed story does not have reproducible official-record evidence."""


def _decimal(value, name, positive=True):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise EvidenceError('invalid_' + name) from None
    if not result.is_finite() or (positive and result <= 0):
        raise EvidenceError('invalid_' + name)
    return result


def _plain(value, name):
    if not isinstance(value, str) or not value.strip() or any(
            char in value for char in ('\n', '\r', '<', '>')):
        raise EvidenceError('invalid_' + name)
    return value.strip()


def _official_url(value):
    try:
        parsed = urlparse(str(value))
    except ValueError:
        raise EvidenceError('unofficial_transaction_source') from None
    if (parsed.scheme != 'https' or parsed.hostname != 'rt.molit.go.kr'
            or parsed.netloc != parsed.hostname or parsed.username or parsed.password):
        raise EvidenceError('unofficial_transaction_source')
    return value


def _read_record(record, case, base_dir):
    if not isinstance(record, dict):
        raise EvidenceError('invalid_record')
    if record.get('cancelled') is not False:
        raise EvidenceError('cancelled_or_unverified_record')
    try:
        date = dt.date.fromisoformat(record['date'])
    except (TypeError, KeyError, ValueError):
        raise EvidenceError('invalid_contract_date') from None
    price = record.get('priceManwon')
    floor = record.get('floor')
    if type(price) is not int or price <= 0 or type(floor) is not int:
        raise EvidenceError('invalid_price_or_floor')
    area = _decimal(record.get('areaSqm'), 'record_area')
    if area != _decimal(case.get('areaSqm'), 'case_area'):
        raise EvidenceError('different_exact_area')
    apartment = _plain(record.get('apartment'), 'record_apartment')
    address = _plain(record.get('address'), 'record_address')
    if apartment != case['apartment'] or address != case['location']:
        raise EvidenceError('different_apartment_or_location')
    source_url = _official_url(record.get('sourceUrl'))
    filename = record.get('evidenceFile')
    if not isinstance(filename, str) or not filename or Path(filename).is_absolute():
        raise EvidenceError('invalid_evidence_path')
    root = Path(base_dir).resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != '.csv':
        raise EvidenceError('invalid_evidence_path')
    try:
        if path.stat().st_size > 12_000_000:
            raise EvidenceError('oversized_official_csv')
        content = path.read_bytes()
    except OSError:
        raise EvidenceError('missing_official_csv') from None
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != record.get('evidenceSha256'):
        raise EvidenceError('official_csv_hash_mismatch')
    try:
        rows = _parse_csv(content, date, date)
    except (UnicodeError, ValueError):
        raise EvidenceError('invalid_official_csv') from None
    expected = (date, address, apartment, area, price, floor)
    if expected not in rows:
        raise EvidenceError('record_not_in_uncancelled_official_csv')
    return {
        'date': date, 'priceManwon': price, 'floor': floor, 'areaSqm': area,
        'address': address, 'apartment': apartment, 'sourceUrl': source_url,
        'evidenceFile': filename, 'evidenceSha256': actual_hash,
    }


def validate_story_data(data, base_dir=ROOT):
    """Validate provenance and exact raw CSV records, returning usable cases.

    Invalid cases are rejected independently.  The caller can still show a
    different verified case; no price, person or replacement case is invented.
    Error codes contain no source payloads, local file contents or credentials.
    """
    if not isinstance(data, dict) or data.get('schemaVersion') != 1:
        raise EvidenceError('invalid_evidence_schema')
    reviewed = data.get('reviewedAt', '')
    try:
        reviewed_stamp = dt.datetime.fromisoformat(reviewed.replace('Z', '+00:00'))
        if reviewed_stamp.tzinfo is None:
            raise ValueError()
    except (AttributeError, TypeError, ValueError):
        raise EvidenceError('invalid_evidence_review_time') from None
    source = data.get('source', {})
    if not isinstance(source, dict):
        raise EvidenceError('invalid_source')
    _official_url(source.get('url'))
    _plain(source.get('provider'), 'source_provider')
    _plain(source.get('method'), 'source_method')
    cases = data.get('cases')
    if not isinstance(cases, list):
        raise EvidenceError('invalid_cases')
    valid, errors, seen = [], [], set()
    for position, case in enumerate(cases):
        try:
            if not isinstance(case, dict):
                raise EvidenceError('invalid_case')
            normalized = dict(case)
            for key in ('id', 'location', 'apartment', 'selectionMethod'):
                normalized[key] = _plain(case.get(key), key)
            if case.get('coverageNote'):
                normalized['coverageNote'] = _plain(case['coverageNote'], 'coverage_note')
            for key in ('fictionalPerson', 'fictionalBackground'):
                if key in case:
                    normalized[key] = _plain(case[key], key)
            try:
                case_reviewed = dt.datetime.fromisoformat(
                    case.get('reviewedAt', reviewed).replace('Z', '+00:00'))
                if case_reviewed.tzinfo is None or case_reviewed > reviewed_stamp:
                    raise ValueError()
            except (AttributeError, TypeError, ValueError):
                raise EvidenceError('invalid_case_review_time') from None
            if normalized['id'] in seen:
                raise EvidenceError('duplicate_case_id')
            seen.add(normalized['id'])
            topics = case.get('topics')
            article_ids = case.get('historicalArticleIds')
            if (not isinstance(topics, list) or not topics
                    or any(topic not in TOPICS for topic in topics)
                    or not isinstance(article_ids, list) or not article_ids
                    or any(not isinstance(item, str) or not item for item in article_ids)):
                raise EvidenceError('invalid_historical_links')
            records = case.get('transactions')
            if not isinstance(records, list) or not 2 <= len(records) <= 12:
                raise EvidenceError('insufficient_transaction_evidence')
            normalized['transactions'] = sorted(
                [_read_record(record, normalized, base_dir) for record in records],
                key=lambda record: (record['date'], record['priceManwon'], record['floor']))
            first, last = normalized['transactions'][0], normalized['transactions'][-1]
            if first['date'] >= last['date']:
                raise EvidenceError('nonpositive_holding_period')
            if first['priceManwon'] >= last['priceManwon']:
                raise EvidenceError('not_an_observed_rising_case')
            normalized['areaSqm'] = _decimal(case.get('areaSqm'), 'case_area')
            normalized['reviewedAt'] = case_reviewed
            valid.append(normalized)
        except (EvidenceError, TypeError, KeyError) as exc:
            errors.append({'casePosition': position,
                           'code': str(exc) if isinstance(exc, EvidenceError) else 'invalid_case_fields'})
    return {'cases': valid, 'errors': errors, 'reviewedAt': reviewed_stamp}


def load_story_evidence(path=EVIDENCE, base_dir=None):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise EvidenceError('evidence_unavailable') from None
    return validate_story_data(data, base_dir=base_dir or path.parent)


def verify_story_evidence(path=EVIDENCE, base_dir=None):
    """Return a JSON-safe integrity report for the publication gate."""
    try:
        evidence = load_story_evidence(path, base_dir)
    except EvidenceError as exc:
        return {'status': 'failed', 'caseCount': 0, 'errors': [{'code': str(exc)}],
                'cases': []}
    return {
        'status': 'passed' if evidence['cases'] and not evidence['errors'] else 'failed',
        'caseCount': len(evidence['cases']), 'errors': evidence['errors'],
        'reviewedAt': evidence['reviewedAt'].isoformat(),
        'cases': [{'id': case['id'], 'recordCount': len(case['transactions']),
                   'apartment': case['apartment'], 'areaSqm': str(case['areaSqm']),
                   'dates': [row['date'].isoformat() for row in case['transactions']],
                   'evidenceSha256': list(dict.fromkeys(row['evidenceSha256']
                                                       for row in case['transactions']))}
                  for case in evidence['cases']],
        'networkUsed': False, 'paidAiUsed': False,
    }


def story_content_id(case):
    """Stable across display dates and changing current-news headlines."""
    return 'housingstory:' + _plain(case.get('id'), 'id')


def select_story(current_news, cases, historical_articles):
    """Match only the existing checked headline topics and historical IDs."""
    by_id = {article['id']: article for article in historical_articles}
    linked_cases = []
    for case in cases:
        references = [by_id[item] for item in case['historicalArticleIds'] if item in by_id]
        if references:
            linked_cases.append((case, references))
    groups = select_comparisons(current_articles(current_news), historical_articles)
    for group in groups:
        for case, references in linked_cases:
            matching = [article for article in references if group['topic'] in article['topics']]
            if group['topic'] in case['topics'] and matching:
                return {'mode': 'matched', 'case': case, 'current': group['current'],
                        'group': group, 'references': matching}
    if linked_cases:
        case, references = linked_cases[0]
        return {'mode': 'reference', 'case': case, 'current': None,
                'group': None, 'references': references}
    return None


def scenario_calculation(case):
    """An explicit, interest-only illustration in nominal KRW ten-thousands.

    The reserves are explicit arbitrary budgets, not calculations of statutory
    taxes or broker fees. The acquisition reserve bundles an unverified tax/
    registration/fee budget. Actual tax liability, capital-gains/property taxes
    and opportunity cost remain uncalculated; never call this after-tax profit.
    """
    first, last = case['transactions'][0], case['transactions'][-1]
    purchase, sale = Decimal(first['priceManwon']), Decimal(last['priceManwon'])
    days = (last['date'] - first['date']).days
    loan = (purchase * SCENARIO_LOAN_RATIO).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    acquisition_reserve = (purchase * SCENARIO_ACQUISITION_RESERVE_RATIO).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP)
    sale_reserve = (sale * SCENARIO_SALE_RESERVE_RATIO).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP)
    holding_budget = SCENARIO_MONTHLY_HOLDING_BUDGET * Decimal(days) / Decimal('365') * Decimal('12')
    reserve = acquisition_reserve + sale_reserve + holding_budget
    interest = loan * SCENARIO_ANNUAL_RATE * Decimal(days) / Decimal('365')
    monthly_interest = loan * SCENARIO_ANNUAL_RATE / Decimal('12')
    capital = purchase - loan + acquisition_reserve
    sale_equity = sale - loan
    illustrative_gain = sale - purchase - reserve - interest
    return {
        'purchase': purchase, 'sale': sale, 'days': days, 'loan': loan,
        'costReserve': reserve, 'initialCapital': capital,
        'totalCashContributed': capital + interest + holding_budget,
        'saleEquityAfterSaleReserveBeforeTax': sale_equity - sale_reserve,
        'acquisitionReserve': acquisition_reserve, 'saleReserve': sale_reserve,
        'holdingBudget': holding_budget,
        'monthlyHoldingBudget': SCENARIO_MONTHLY_HOLDING_BUDGET,
        'interest': interest, 'monthlyInterest': monthly_interest,
        'saleEquityBeforeTax': sale_equity,
        'illustrativeGainBeforeTax': illustrative_gain,
        'priceChangePct': (sale / purchase - Decimal('1')) * Decimal('100'),
        'stressAdditionalMonthlyInterest': loan * Decimal('0.02') / Decimal('12'),
        'stressSaleEquity': sale * Decimal('0.8') - loan,
    }


def _amount(value):
    value = Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return f'{value:,.0f}만원'


def _heading(now):
    weekday = ('월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일')[now.weekday()]
    return f'📚 [실거래로 읽는 투자 재구성]\n{now.year % 100}년 {now.month}월 {now.day}일 {weekday}'


def _unavailable(now):
    return '\n\n'.join([
        _heading(now),
        '검증된 국토부 거래 원본을 확인하지 못해 이번 투자 스토리를 제공하지 않습니다. 가격·대출·성공 인물을 만들어 채우지 않습니다.',
        '기존 부동산 뉴스와 과거 기사·6개월·1년·2년 서울 지수 비교는 각각 별도 콘텐츠에서 확인할 수 있습니다.',
        f'갱신 확인: {now:%Y-%m-%d %H:%M} (한국시간)',
        '자료: 국토교통부 실거래가 공개시스템 (무료 공식 공개 CSV)\n' + PAGE,
    ])


def build_story_section(current_news, get, now, catalog_path=CATALOG,
                        evidence_path=EVIDENCE, story_data=None, evidence_base_dir=None):
    """Build one free story card; ``get`` is deliberately never called.

    ``story_data`` supports isolated fixtures.  Real publication loads the
    reviewed evidence file and proves every displayed contract against its
    retained official CSV and SHA-256.  Scenario values are not evidence values.
    """
    now = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
    try:
        evidence = (validate_story_data(story_data, evidence_base_dir or ROOT)
                    if story_data is not None else load_story_evidence(evidence_path, evidence_base_dir))
        catalog = json.loads(Path(catalog_path).read_text(encoding='utf-8'))
        selection = select_story(current_news, evidence['cases'], catalog['articles'])
        if selection is None:
            return _unavailable(now)
    except (EvidenceError, OSError, ValueError, KeyError, TypeError):
        return _unavailable(now)
    case = selection['case']
    first, last = case['transactions'][0], case['transactions'][-1]
    model = scenario_calculation(case)
    person = case.get('fictionalPerson', '김 씨')
    background = case.get('fictionalBackground', '그는 기존 저축과 살던 집을 정리한 현금을 가진 사람으로 설정했습니다. 작은 종잣돈이나 무자본으로 누구나 부자가 된 이야기가 아닙니다. 가족과 오래 살 집을 찾던 그는 대책 기사만 읽고 매수 버튼을 누르지 않았습니다. 출퇴근 거리와 단지 상태를 직접 확인하고, 같은 면적의 계약 가격을 비교한 뒤에도 잔금에 쓸 돈과 매달 남는 돈을 먼저 적었습니다.')
    blocks = [_heading(now),
              '구분: [실거래 확인] 실제 거래기록으로 확인한 가격 사례 + [가상 인물·재구성] 창작한 투자 과정입니다. 실존 인물의 성공담·실제 매수인 수익을 확인한 전기가 아닙니다.',
              f"사례 ID: {case['id']}\n{case['location']} · {case['apartment']} · 전용 {case['areaSqm'].normalize():f}㎡"]
    if selection['current']:
        group = selection['group']
        blocks.extend([
            '현재 기사: ' + selection['current']['title'] + '\n' + selection['current']['url'],
            '연결한 주제: ' + group['label'] + '\n유사점: 현재 제목에서 확인한 표현은 ‘' + '·'.join(group['hits'])
            + '’입니다. 당시 기사와 주제가 겹칩니다. 같은 가격 상승이 반복된다는 신호는 아닙니다.',
        ])
    else:
        blocks.append('연결 상태: 오늘 기사와 직접 연결할 검증된 주제를 찾지 못했습니다. 아래는 오늘의 유사 사례로 제시하는 것이 아닌 과거 실거래 참고 사례입니다.')
    for article in selection['references']:
        blocks.append(f"과거 기사 · {article['era']} · {article['date']}\n{article['title']}\n{article['url']}")
        blocks.append('당시 상황: ' + article['fact'] + '\n현재와의 차이: ' + article['difference'])
        if article['date'] > first['date'].isoformat():
            blocks.append('기사와 계약의 순서: 이 과거 기사는 첫 거래 뒤에 나왔습니다. 가상 인물의 매입 당시 판단 근거로 거꾸로 사용하지 않습니다.')
    blocks.append('① [실거래 확인] 어떤 집의 거래가 올랐나\n표본 선정: ' + case['selectionMethod'])
    if case.get('coverageNote'):
        blocks.append('조회 범위와 빈 구간: ' + case['coverageNote'])
    records = []
    for record in case['transactions']:
        records.append(f"계약 {record['date']:%Y-%m-%d} · {record['floor']}층 · 전용 {record['areaSqm'].normalize():f}㎡ · {_amount(record['priceManwon'])}")
    blocks.append('\n'.join(records))
    blocks.append(f"첫·마지막 표본 가격 차이: {_amount(model['sale'] - model['purchase'])} ({model['priceChangePct']:+.2f}%) · 계약일 간격 {model['days']:,}일\n동일 단지·동일 전용면적의 서로 다른 공개 계약을 비교했습니다. 동일 호실·동일 소유자·실제 반복 매매 여부는 확인할 수 없으며, 단지 전체의 평균 상승률도 아닙니다.")
    declines = [(earlier, later) for earlier, later
                in zip(case['transactions'], case['transactions'][1:])
                if later['priceManwon'] < earlier['priceManwon']]
    if declines:
        earlier, later = min(declines, key=lambda pair: Decimal(pair[1]['priceManwon'])
                             / Decimal(pair[0]['priceManwon']))
        change = (Decimal(later['priceManwon']) / Decimal(earlier['priceManwon']) - 1) * 100
        blocks.append(f"중간 변동도 확인: {earlier['date']:%Y-%m-%d} {earlier['floor']}층 {_amount(earlier['priceManwon'])} → {later['date']:%Y-%m-%d} {later['floor']}층 {_amount(later['priceManwon'])} ({change:+.2f}%). 서로 다른 층·호실의 표본 가격 차이이며 같은 집의 하락률이 아닙니다. 가격이 계속 직선으로 오른 것으로 읽지 마세요.")
    if model['illustrativeGainBeforeTax'] <= 0:
        blocks.append('비용을 반영한 위 가정에서는 예시 차익이 0 이하입니다. 거래 가격이 올랐다는 이유만으로 투자에 성공했다고 해석할 수 없습니다.')
    blocks.extend([
        f'② [가상 인물·재구성] 가격표보다 먼저 가계부를 펼치다\n아래의 {person}는 창작한 인물입니다. {background} 이 자금의 유래·심리·현장 방문은 실제 매수인의 행동을 확인한 내용이 아닙니다.',
        '③ [비용 가정] 당시 대출 승인을 뜻하지 않습니다\n'
        + f"첫 거래 가격 {_amount(model['purchase'])}에 매수했다고 가정합니다. 대출은 가격의 30%인 {_amount(model['loan'])}, 자기자금은 {_amount(model['purchase'] - model['loan'])}입니다. 취득세·중개·등기 등을 합친 매수 부대비용 예산을 가격의 4%인 {_amount(model['acquisitionReserve'])}으로 정하면 시작 자금은 {_amount(model['initialCapital'])}입니다. 매각 부대비용은 양도세를 제외한 매도가의 1%인 {_amount(model['saleReserve'])}, 보유 중 관리·소수선 예산은 월 {_amount(model['monthlyHoldingBudget'])}, 전체 기간 예산 {_amount(model['holdingBudget'])}을 전액 쓴다고 가정합니다. 예산은 실제 세율·중개보수·수리비를 조회해 확정한 금액이 아니며, 30% 대출도 개인별 한도·심사·당시 규제 적용을 확인한 승인이 아닙니다.",
        '④ 버티는 시간 — 통장에서는 이자가 먼저 빠져나간다\n'
        + f"가상 대출은 연 4.5% 고정, 원금 상환 없이 이자만 내고 매도 때 원금을 갚는 단순 모델입니다. 월 이자는 {_amount(model['monthlyInterest'])}, 보유 예산을 합친 월 지출은 {_amount(model['monthlyInterest'] + model['monthlyHoldingBudget'])}, {model['days']:,}일 이자는 365일 기준 {_amount(model['interest'])}입니다. 이자와 보유 예산을 시작 자금에 더한 보유 종료까지의 누적 현금 투입은 {_amount(model['totalCashContributed'])}입니다. {person}에게 필요한 것은 호가가 오르는 화면보다 이 지출과 별도 생활비를 계속 감당할 소득이었습니다. 실제 대출의 만기·상환 방식·금리 변경·규제는 이 모델과 다릅니다.",
        '⑤ 오른 뒤의 선택 — 평가액과 손에 쥔 돈을 나누다\n'
        + f"마지막 표본 가격 {_amount(model['sale'])}에 팔 수 있었다고 가정하면, 대출 원금을 갚은 직후 금액은 {_amount(model['saleEquityBeforeTax'])}입니다. 매각 예산까지 빼면 {_amount(model['saleEquityAfterSaleReserveBeforeTax'])}이며 이것이 전부 번 돈은 아닙니다. 여기서 위 누적 현금 투입을 차감한 세금 미정산 예시 차익은 {_amount(model['illustrativeGainBeforeTax'])}입니다. 이는 매도가에서 매수가·매수/매각/보유 예산·대출 이자를 빼는 것과 같습니다. 취득세 예산과 실제 세액의 차이, 보유세·양도세 및 실제 중개·수리·이사 비용 등을 확정 계산하지 않았으므로 세후 순이익이나 확정 수익률로 제시하지 않습니다.",
        '⑥ 성공 스토리의 이면 — 팔리지 않는 달을 견딜 수 있었을까\n'
        + f"연 금리가 가정치보다 2%p 높으면 이자 부담이 월 {_amount(model['stressAdditionalMonthlyInterest'])} 늘어납니다. 마지막 표본보다 20% 낮은 가격에 처분하면 대출 상환 후 잔액은 {_amount(model['stressSaleEquity'])}입니다. 이 또한 실제로 관측한 하락이 아닌 스트레스 가정입니다. 급한 매도·실직·수리비·거래 공백과 세금은 이야기의 성공 결말을 바꿀 수 있습니다.",
        '⑦ [한계] 이 사례가 보여주는 것\n이 재구성은 이미 확인된 상승 표본을 뒤에서 골라 읽는 역사 사례입니다. 실제 누군가가 이 경로로 부자가 됐다는 증거가 아니며, 현재 같은 집을 사면 같은 결과가 난다는 예측도 아닙니다. 같은 지역의 다른 단지·층·동·향·수리 상태와 거래 시점에 따라 결과가 달라집니다. 자기자금을 다른 곳에 두었을 때의 기회비용도 계산하지 않았습니다. 과거 기사·정책과 가격 차이 사이의 인과관계도 이 두 거래만으로 입증할 수 없습니다.',
        f"갱신 확인: {now:%Y-%m-%d %H:%M} (한국시간)\n거래 원본 검토: {case['reviewedAt'].astimezone(KST):%Y-%m-%d %H:%M} (한국시간)\n과거 계약 사례의 원본 검토일이며 오늘 새로 발생한 거래라는 뜻은 아닙니다.",
        '자료: 국토교통부 실거래가 공개시스템 (무료 공식 공개 CSV). 해제 표시가 없는 동일 단지·동일 전용면적 계약을 보관 원본과 SHA-256으로 대조했습니다. 신고·정정·해제로 공개 기록이 바뀔 수 있습니다.\n' + PAGE,
        '출처: 과거 기사 원문은 위 상세 링크에서 확인할 수 있습니다. 인물·심리·의사결정·대출·비용 예비금·스트레스 상황은 설명을 위한 창작·가정이며 공식 거래자료의 사실과 구분했습니다.',
    ])
    return '\n\n'.join(blocks)
