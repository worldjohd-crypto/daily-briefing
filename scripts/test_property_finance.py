"""Offline checks for current-month official rates and honest source failures."""

import datetime as dt
import unittest
from unittest.mock import Mock

import requests

import property_finance as finance
from content_rendering import split_content_sources


def product(didimdol=False, loan='3.6'):
    applicant = '<li>민법상 성년</li>' + ('<li>본인 및 배우자 합산 순자산 가액 5.11억원 이하</li>' if didimdol else '')
    groups = [
        ('신청대상', applicant),
        ('대출요건', '<li>6억원 이하 주택</li><li>부부합산 소득 7천만원 이하</li><li>LTV 최대 70%</li><li>DTI 최대 60%</li>'),
        ('상품구조', f'<li>대출한도 최대 {loan}억원</li><li>(특례대상은 별도 한도)</li><li>대출만기 10년, 30년</li>'),
    ]
    return ('<title>' + ('디딤돌대출' if didimdol else '보금자리론') + ' | 한국주택금융공사</title>'
            + '<div id="cms-content"><div class="product_top">' + ''.join(
                f'<dl><dt>{label}</dt><dd><ul class="list_ul03">{body}</ul></dd></dl>' for label, body in groups)
            + '</div></div>')


def rates(didimdol=False, month='2026-10', day='2026년 10월 01일', first='5.00'):
    terms = ['10년', '15년', '20년', '30년'] + ([] if didimdol else ['40년', '50년'])
    labels = ['20백만원 이하', '20백만원 초과~40백만원 이하', '40백만원 초과~70백만원 이하', '70백만원 초과~85백만원 이하'] if didimdol else ['u-보금자리론', '아낌e보금자리론', 't-보금자리론']
    return ('<title>' + ('디딤돌대출' if didimdol else '보금자리론') + ' | 한국주택금융공사</title>'
            + f'<div class="board-area"><input class="b-dt" value="{month}"><div class="conTsearch"><span>{month[:4]}년 {int(month[5:])}월 금리</span></div>'
            + f'<p>공시일 : {day}</p><table><thead><tr><th>' + ('소득수준 (부부합산)' if didimdol else '상품별/만기')
            + '</th>' + ''.join('<th>' + term + '</th>' for term in terms) + '</tr></thead><tbody>'
            + ''.join('<tr><td>' + label + '</td><td>' + '</td><td>'.join([first] + ['5.10'] * (len(terms) - 1)) + '</td></tr>' for label in labels)
            + '</tbody></table></div>')


class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 17, 10, tzinfo=finance.KST)

    def getter(self, overrides=None):
        pages = {finance.HF_HOMELOAN: product(), finance.HF_HOMELOAN_RATES: rates(),
                 finance.HF_DIDIMDOL: product(True), finance.HF_DIDIMDOL_RATES: rates(True)}
        pages.update(overrides or {})
        return Mock(side_effect=lambda url, **kwargs: Mock(status_code=200, text=pages[url]))

    def test_real_values_are_fetched_and_changed_each_build_with_no_cache(self):
        get = self.getter()
        body = finance.build_housing_finance(get, self.now)
        self.assertEqual(get.call_count, 4)
        self.assertIn('5.00 / 5.10', body)
        self.assertIn('2026년 10월 · 공시일 2026-10-01', body)
        self.assertIn('순자산 가액 5.11억원', body)
        self.assertIn('(특례대상은 별도 한도)', body)
        changed = finance.build_housing_finance(self.getter({finance.HF_HOMELOAN: product(loan='3.9'), finance.HF_HOMELOAN_RATES: rates(first='4.88')}), self.now)
        self.assertIn('최대 3.9억원', changed)
        self.assertIn('4.88 / 5.10', changed)
        self.assertNotIn('최대 3.6억원', changed.split('디딤돌대출 (일반)')[0])

    def test_old_month_future_date_invalid_rate_and_schema_fail_without_reusing_figures(self):
        invalid = [rates(month='2026-09'), rates(day='2026년 10월 18일'), rates(first='NaN'),
                   rates().replace('<th>10년</th>', '<th>12년</th>'), rates().replace('u-보금자리론', '다른상품')]
        for html in invalid:
            with self.subTest(html=html[:100]):
                body = finance.build_housing_finance(self.getter({finance.HF_HOMELOAN_RATES: html}), self.now)
                bogeumjari = body.split('디딤돌대출 (일반)')[0]
                self.assertIn('이번 달 공시금리: 최신 공식 내용을 확인하지 못했습니다', bogeumjari)
                self.assertNotIn('5.00 / 5.10', bogeumjari)
                self.assertIn('디딤돌대출 (일반)\n조건:', body)

    def test_missing_terms_do_not_fall_back_to_old_evergreen_conditions(self):
        body = finance.build_housing_finance(self.getter({finance.HF_DIDIMDOL: product(True).replace('순자산', '다른말')}), self.now)
        self.assertIn('상품 조건: 최신 공식 내용을 확인하지 못했습니다', body)
        self.assertNotIn('순자산', body)
        self.assertIn('금리 기준: 2026년 10월', body)

    def test_offline_or_wrong_document_has_honest_failure_and_source_links(self):
        for get in (Mock(side_effect=requests.ConnectionError('offline')),
                    self.getter({url: '<title>로그인</title>' for url in finance.SOURCES['housingfinance']['sources']})):
            body = finance.build_housing_finance(get, self.now)
            self.assertEqual(body.count('최신 공식 내용을 확인하지 못했습니다'), 4)
            self.assertNotIn('5.00', body)
            self.assertIn(finance.HF_HOMELOAN, body)

    def test_kst_month_boundary_and_sources_remain_outside_copy(self):
        now = dt.datetime(2026, 9, 30, 15, 1, tzinfo=dt.timezone.utc)
        body = finance.build_housing_finance(self.getter(), now)
        self.assertIn('수집: 2026-10-01 00:01 (한국시간)', body)
        copy, sources = split_content_sources(body)
        self.assertNotIn('자료:', copy)
        self.assertNotIn('https://', copy)
        self.assertIn('금리 기준:', copy)
        self.assertTrue(sources)

    def test_timed_out_didimdol_product_recovers_with_one_fresh_request(self):
        normal = self.getter()
        calls = []

        def intermittent(url, **kwargs):
            calls.append(url)
            if url == finance.HF_DIDIMDOL and calls.count(url) == 1:
                raise requests.ReadTimeout('temporary slow official response')
            return normal(url, **kwargs)

        body = finance.build_housing_finance(intermittent, self.now)
        self.assertEqual(calls.count(finance.HF_DIDIMDOL), 2)
        self.assertEqual(len(calls), 5)
        self.assertIn('순자산 가액 5.11억원', body)
        self.assertNotIn('확인하지 못했습니다', body)

    def test_network_retry_is_bounded_and_never_uses_old_conditions(self):
        for failure in (requests.ReadTimeout, requests.ConnectTimeout, requests.ConnectionError):
            with self.subTest(failure=failure.__name__):
                normal = self.getter()
                calls = []

                def unavailable_product(url, **kwargs):
                    calls.append(url)
                    if url == finance.HF_DIDIMDOL:
                        raise failure('still unavailable')
                    return normal(url, **kwargs)

                body = finance.build_housing_finance(unavailable_product, self.now)
                self.assertEqual(calls.count(finance.HF_DIDIMDOL), 2)
                self.assertEqual(len(calls), 5)
                self.assertEqual(body.count('확인하지 못했습니다'), 1)
                self.assertNotIn('순자산', body)
                self.assertEqual(body.count('공시일 2026-10-01'), 2)

    def test_http_and_schema_errors_are_not_retried(self):
        http = Mock(status_code=503)
        http.raise_for_status.side_effect = requests.HTTPError('503 from official page')
        get = Mock(return_value=http)
        with self.assertRaises(requests.HTTPError):
            finance._page(get, finance.HF_DIDIMDOL, '디딤돌대출')
        self.assertEqual(get.call_count, 1)

        for malformed in ('<title>로그인</title>', product(True).replace('순자산', '다른말')):
            get = self.getter({finance.HF_DIDIMDOL: malformed})
            body = finance.build_housing_finance(get, self.now)
            self.assertEqual(get.call_count, 4)
            self.assertEqual(sum(call.args[0] == finance.HF_DIDIMDOL for call in get.call_args_list), 1)
            self.assertEqual(body.count('상품 조건: 최신 공식 내용을 확인하지 못했습니다'), 1)


if __name__ == '__main__':
    unittest.main()
