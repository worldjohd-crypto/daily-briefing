"""Data integrity checks for direct official metrics (no network or AI)."""

import datetime as dt
import unittest
from unittest.mock import Mock

import official_metrics as metrics


class OfficialMetricsTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 17, 1, tzinfo=metrics.KST)

    def _fng(self, date, value='63'):
        stamp = int(dt.datetime.combine(date, dt.time(), metrics.UTC).timestamp())
        return {'value': value, 'timestamp': str(stamp), 'value_classification': 'Greed'}

    def test_cryptofear_uses_provider_date_and_exact_previous_days(self):
        rows = [self._fng(dt.date(2026, 10, 16)), self._fng(dt.date(2026, 10, 15), '53')]
        get = Mock(return_value=Mock(json=lambda: {'data': rows, 'metadata': {'error': None}}))
        text = metrics.build_cryptofear(get, self.now)
        self.assertIn('2026년 10월 16일 (UTC)', text)
        self.assertIn('전일 대비 +10포인트', text)
        self.assertIn(metrics.FNG_SOURCE, text)
        self.assertNotIn('7일 전:', text)

    def test_cryptofear_rejects_stale_and_out_of_range_values(self):
        for date, value in ((dt.date(2026, 10, 1), '63'), (dt.date(2026, 10, 16), '101')):
            get = Mock(return_value=Mock(json=lambda: {'data': [self._fng(date, value)]}))
            self.assertIn('최신 공식 지수를 확인하지 못했습니다', metrics.build_cryptofear(get, self.now))

    def _wiki(self, article, start, values):
        return {'items': [dict(project='en.wikipedia', article=article, access='all-access', agent='user',
                               granularity='daily', timestamp=(start + dt.timedelta(days=i)).strftime('%Y%m%d00'), views=n)
                          for i, n in enumerate(values)]}

    def test_wiki_requires_all_fourteen_unique_dates(self):
        start, end = dt.date(2026, 10, 1), dt.date(2026, 10, 14)
        good = self._wiki('Bitcoin', start, [10] * 7 + [20] * 7)
        self.assertEqual(metrics._daily_views(good, 'Bitcoin', start, end), (140, 70))
        for rows in (good['items'][:-1], good['items'] + good['items'][:1]):
            with self.assertRaises(ValueError):
                metrics._daily_views({'items': rows}, 'Bitcoin', start, end)

    def test_wiki_rejects_bot_traffic_and_marks_failures_without_fabrication(self):
        start, end = dt.date(2026, 10, 1), dt.date(2026, 10, 14)
        payload = self._wiki('Bitcoin', start, [10] * 14)
        payload['items'][0]['agent'] = 'spider'
        with self.assertRaises(ValueError):
            metrics._daily_views(payload, 'Bitcoin', start, end)
        get = Mock(side_effect=metrics.requests.ConnectionError('offline'))
        text = metrics.build_wikiinterest(get, self.now)
        self.assertIn('최근 7일: 2026-10-08 ~ 2026-10-14 (UTC)', text)
        self.assertIn('14일 전체 자료 미확인:', text)
        self.assertNotIn('0회 ·', text)
        self.assertIn(metrics.WIKI_SOURCE, text)


if __name__ == '__main__':
    unittest.main()
