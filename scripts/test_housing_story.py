"""Offline fixtures prove provenance, honest labels, arithmetic and copy scope."""
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from content_rendering import split_content_sources
import housing_story as hs


NOW = dt.datetime(2026, 10, 18, 10, 15, tzinfo=hs.KST)
NEWS = ('🏠 [부동산 주요뉴스]\n26년 10월 18일 일요일\n\n'
        '대출 규제 강화로 매수 대기\nhttps://n.news.naver.com/mnews/article/001/123456')
HEADER = ['시군구', '단지명', '전용면적(㎡)', '계약년월', '계약일',
          '거래금액(만원)', '층', '해제사유발생일']


def make_fixture(folder, prices=(20000, 30000)):
    """Synthetic records exist only in a temporary test directory."""
    dates = ('2017-08-03', '2019-08-03')
    rows = [
        ['테스트시 테스트구', '테스트아파트', '84.89', date[:7].replace('-', ''),
         str(int(date[-2:])), str(price), str(floor), '']
        for date, price, floor in zip(dates, prices, (5, 8))
    ]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADER)
    writer.writerows(rows)
    content = out.getvalue().encode('utf-8-sig')
    path = Path(folder) / 'synthetic-test-only.csv'
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    data = {
        'schemaVersion': 1, 'reviewedAt': NOW.isoformat(),
        'source': {'provider': '국토교통부 실거래가 공개시스템',
                   'url': hs.PAGE, 'method': '합성 테스트 fixture — 실제 공개 자료 아님'},
        'cases': [{
            'id': 'synthetic-only', 'location': '테스트시 테스트구',
            'apartment': '테스트아파트', 'areaSqm': '84.89',
            'historicalArticleIds': ['moon-credit-20170802'], 'topics': ['credit'],
            'selectionMethod': '합성 테스트 입력, 실제 거래로 게시하지 않음',
            'transactions': [{
                'date': date, 'priceManwon': price, 'floor': floor,
                'areaSqm': '84.89', 'address': '테스트시 테스트구',
                'apartment': '테스트아파트', 'cancelled': False,
                'evidenceFile': path.name, 'evidenceSha256': digest,
                'sourceUrl': hs.PAGE,
            } for date, price, floor in zip(dates, prices, (5, 8))],
        }],
    }
    return data, path


class HousingStoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.data, self.raw = make_fixture(self.folder.name)

    def body(self, news=NEWS, data=None):
        return hs.build_story_section(
            news, lambda *args, **kwargs: self.fail('story must never fetch another source'),
            NOW, story_data=data if data is not None else self.data,
            evidence_base_dir=self.folder.name)

    def test_exact_official_csv_tuple_verified_and_safe_report_serializes(self):
        validated = hs.validate_story_data(self.data, self.folder.name)
        self.assertEqual(validated['errors'], [])
        case = validated['cases'][0]
        self.assertEqual(case['transactions'][0]['priceManwon'], 20000)
        self.assertEqual(hs.story_content_id(case), 'housingstory:synthetic-only')
        path = Path(self.folder.name) / 'evidence.json'
        path.write_text(json.dumps(self.data), encoding='utf-8')
        report = hs.verify_story_evidence(path)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['caseCount'], 1)
        self.assertEqual(report['cases'][0]['recordCount'], 2)
        self.assertFalse(report['networkUsed'])
        json.dumps(report)

    def test_displayed_price_matches_raw_not_merely_metadata(self):
        bad = copy.deepcopy(self.data)
        bad['cases'][0]['transactions'][1]['priceManwon'] = 90000
        validated = hs.validate_story_data(bad, self.folder.name)
        self.assertEqual(validated['cases'], [])
        self.assertEqual(validated['errors'][0]['code'],
                         'record_not_in_uncancelled_official_csv')
        self.assertIn('이번 투자 스토리를 제공하지 않습니다', self.body(data=bad))
        self.assertNotIn('90,000만원', self.body(data=bad))

    def test_tampered_csv_hash_rejects(self):
        self.raw.write_bytes(self.raw.read_bytes() + b'\n')
        validated = hs.validate_story_data(self.data, self.folder.name)
        self.assertEqual(validated['errors'][0]['code'], 'official_csv_hash_mismatch')

    def test_cancelled_raw_row_is_rejected_even_with_correct_hash(self):
        content = self.raw.read_bytes().decode('utf-8-sig')
        content = content.replace('30000,8,', '30000,8,20190804')
        self.raw.write_bytes(content.encode('utf-8-sig'))
        for row in self.data['cases'][0]['transactions']:
            row['evidenceSha256'] = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        validated = hs.validate_story_data(self.data, self.folder.name)
        self.assertEqual(validated['cases'], [])
        self.assertEqual(validated['errors'][0]['code'],
                         'record_not_in_uncancelled_official_csv')

    def test_cancelled_flag_or_missing_flag_rejects(self):
        for flag in (True, None):
            with self.subTest(flag=flag):
                bad = copy.deepcopy(self.data)
                bad['cases'][0]['transactions'][0]['cancelled'] = flag
                self.assertEqual(hs.validate_story_data(bad, self.folder.name)['cases'], [])

    def test_exact_area_and_apartment_required(self):
        for field, value in (('areaSqm', '84.9'), ('apartment', '다른아파트'),
                             ('address', '다른구')):
            with self.subTest(field=field):
                bad = copy.deepcopy(self.data)
                bad['cases'][0]['transactions'][1][field] = value
                self.assertEqual(hs.validate_story_data(bad, self.folder.name)['cases'], [])

    def test_integer_money_and_finite_area_required(self):
        for field, value in (('priceManwon', True), ('priceManwon', 30000.0),
                             ('priceManwon', -5), ('areaSqm', 'NaN')):
            with self.subTest(field=field, value=value):
                bad = copy.deepcopy(self.data)
                bad['cases'][0]['transactions'][1][field] = value
                self.assertEqual(hs.validate_story_data(bad, self.folder.name)['cases'], [])

    def test_external_source_and_parent_path_rejected(self):
        for field, value in (('sourceUrl', 'https://example.com/transactions.csv'),
                             ('sourceUrl', 'https://rt.molit.go.kr.evil.com/x'),
                             ('sourceUrl', 'https://rt.molit.go.kr:443/x'),
                             ('evidenceFile', '../private.csv')):
            with self.subTest(field=field):
                bad = copy.deepcopy(self.data)
                bad['cases'][0]['transactions'][1][field] = value
                self.assertEqual(hs.validate_story_data(bad, self.folder.name)['cases'], [])

    def test_falling_pair_is_not_presented_as_success(self):
        data, _ = make_fixture(self.folder.name, prices=(30000, 20000))
        validated = hs.validate_story_data(data, self.folder.name)
        self.assertEqual(validated['errors'][0]['code'], 'not_an_observed_rising_case')
        self.assertIn('이번 투자 스토리를 제공하지 않습니다', self.body(data=data))

    def test_independent_cashflow_calculation_uses_elapsed_days_and_both_cost_sides(self):
        case = hs.validate_story_data(self.data, self.folder.name)['cases'][0]
        model = hs.scenario_calculation(case)
        self.assertEqual(model['days'], 730)
        self.assertEqual(model['loan'], Decimal('6000'))
        self.assertEqual(model['initialCapital'], Decimal('14800'))
        self.assertEqual(model['monthlyInterest'], Decimal('22.5'))
        self.assertEqual(model['interest'], Decimal('540'))
        self.assertEqual(model['acquisitionReserve'], Decimal('800'))
        self.assertEqual(model['saleReserve'], Decimal('300'))
        self.assertEqual(model['holdingBudget'], Decimal('360'))
        self.assertEqual(model['illustrativeGainBeforeTax'], Decimal('8000'))
        self.assertEqual(model['totalCashContributed'], Decimal('15700'))
        self.assertEqual(model['saleEquityAfterSaleReserveBeforeTax'], Decimal('23700'))
        self.assertEqual(model['saleEquityBeforeTax'], Decimal('24000'))
        self.assertEqual(model['priceChangePct'], Decimal('50.0'))
        self.assertEqual(model['stressAdditionalMonthlyInterest'], Decimal('10'))
        self.assertEqual(model['stressSaleEquity'], Decimal('18000'))

    def test_matched_story_labels_fiction_and_never_confuses_owner_profit(self):
        body = self.body()
        for text in ('[실거래 확인]', '[가상 인물·재구성]', '[비용 가정]', '[한계]',
                     '현재 기사: 대출 규제 강화로 매수 대기', '문재인 정부',
                     '동일 호실·동일 소유자', '세후 순이익', '연 4.5%',
                     '월 이자는 23만원', '14,800만원', '8,000만원',
                     '세금 미정산 예시 차익', '매수 부대비용', '매각 부대비용',
                     '보유 중 관리·소수선', '26년 10월 18일 일요일'):
            self.assertIn(text, body)
        self.assertNotIn('노무현 정부', body)
        self.assertGreater(len(body), 2500)

    def test_unrelated_obituary_is_reference_mode_and_no_forced_link(self):
        body = self.body('건설사 대표 부고\nhttps://n.news.naver.com/mnews/article/001/123456')
        self.assertIn('오늘 기사와 직접 연결할 검증된 주제를 찾지 못했습니다', body)
        self.assertNotIn('현재 기사:', body)
        self.assertNotIn('유사점:', body)
        self.assertIn('과거 실거래 참고 사례', body)

    def test_topics_must_match_verified_historical_article(self):
        bad = copy.deepcopy(self.data)
        bad['cases'][0]['historicalArticleIds'] = ['does-not-exist']
        self.assertIn('이번 투자 스토리를 제공하지 않습니다', self.body(data=bad))
        # A supply headline cannot be attached to a credit-only evidence case.
        body = self.body('신규 택지 공급\nhttps://n.news.naver.com/mnews/article/001/123456')
        self.assertNotIn('현재 기사:', body)

    def test_copy_keeps_detail_links_and_model_but_excludes_attribution(self):
        content, sources = split_content_sources(self.body())
        self.assertIn('https://n.news.naver.com/mnews/article/001/123456', content)
        self.assertIn('https://news.sbs.co.kr/news/endPage.do?news_id=N1004327722', content)
        self.assertIn('8,000만원', content)
        self.assertNotIn('자료:', content)
        self.assertNotIn('출처:', content)
        self.assertNotIn(hs.PAGE, content)
        self.assertIn(hs.PAGE, sources)
        self.assertIn('자료:', sources)

    def test_missing_file_graceful_and_no_network_or_ai(self):
        body = hs.build_story_section(NEWS, lambda *a, **k: self.fail('network called'),
                                     NOW, evidence_path=Path(self.folder.name) / 'missing.json')
        self.assertIn('가격·대출·성공 인물을 만들어 채우지 않습니다', body)
        self.assertIn('갱신 확인: 2026-10-18 10:15', body)
        self.assertNotIn('현재 기사:', body)

    def test_content_id_does_not_include_display_date_or_news(self):
        case = hs.validate_story_data(self.data, self.folder.name)['cases'][0]
        self.assertEqual(hs.story_content_id(case), 'housingstory:synthetic-only')
        changed = dict(case, reviewedAt=NOW + dt.timedelta(days=1))
        self.assertEqual(hs.story_content_id(case), hs.story_content_id(changed))

    def test_small_rise_with_cost_loss_remains_explicit(self):
        data, _ = make_fixture(self.folder.name, prices=(20000, 20100))
        body = self.body(data=data)
        self.assertIn('예시 차익이 0 이하', body)
        self.assertIn('세금 미정산 예시 차익은 -', body)

    def test_intermediate_lower_contract_is_shown_without_claiming_same_home_drop(self):
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(HEADER)
        writer.writerows([
            ['테스트시 테스트구', '테스트아파트', '84.89', '201708', '3', '20000', '5', ''],
            ['테스트시 테스트구', '테스트아파트', '84.89', '201808', '3', '40000', '11', ''],
            ['테스트시 테스트구', '테스트아파트', '84.89', '201908', '3', '30000', '8', ''],
        ])
        self.raw.write_bytes(out.getvalue().encode('utf-8-sig'))
        record = dict(self.data['cases'][0]['transactions'][0],
                      date='2018-08-03', priceManwon=40000, floor=11)
        self.data['cases'][0]['transactions'].append(record)
        for item in self.data['cases'][0]['transactions']:
            item['evidenceSha256'] = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        body = self.body()
        self.assertIn('2018-08-03 11층 40,000만원 → 2019-08-03 8층 30,000만원 (-25.00%)', body)
        self.assertIn('같은 집의 하락률이 아닙니다', body)
        self.assertIn('첫·마지막 표본 가격 차이: 10,000만원 (+50.00%)', body)

    def test_article_after_purchase_is_not_used_as_purchase_reason(self):
        self.data['cases'][0]['historicalArticleIds'] = ['moon-supply-20180921']
        self.data['cases'][0]['topics'] = ['supply']
        body = self.body('신규 택지 공급\nhttps://n.news.naver.com/mnews/article/001/123456')
        self.assertIn('이 과거 기사는 첫 거래 뒤에 나왔습니다', body)
        self.assertIn('매입 당시 판단 근거로 거꾸로 사용하지 않습니다', body)

    def test_unobserved_horizon_is_explicit_not_synthetic(self):
        self.data['cases'][0]['coverageNote'] = '6개월 뒤 같은 면적의 공개 거래는 0건이며 가격을 보간하지 않았습니다.'
        body = self.body()
        self.assertIn('6개월 뒤 같은 면적의 공개 거래는 0건', body)
        self.assertNotIn('이후 6개월:', body)


if __name__ == '__main__':
    unittest.main()
