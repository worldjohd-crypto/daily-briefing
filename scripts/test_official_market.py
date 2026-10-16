import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import official_market as market


class OfficialMarketTests(unittest.TestCase):
    def test_ecb_conversion_and_publication_date(self):
        xml = b'<Cube><Cube time="2026-10-16"><Cube currency="KRW" rate="1500"/><Cube currency="USD" rate="1.2"/><Cube currency="JPY" rate="180"/></Cube></Cube>'
        get = lambda *a, **k: SimpleNamespace(content=xml, raise_for_status=lambda: None)
        date, rates = market.ecb_rates(get, datetime.datetime(2026, 10, 17))
        self.assertEqual(str(date), '2026-10-16')
        self.assertEqual(rates['USD'], 1250)
        self.assertAlmostEqual(rates['JPY'], 833.333333333)
        with patch.dict(market.os.environ, {}, clear=True):
            body = market.build_fuelfx(get, datetime.datetime(2026, 10, 17))
        self.assertIn('2026-10-16 발표', body)
        self.assertIn('은행 매매기준율이 아닙니다', body)
        self.assertIn('인증키 연결 전까지 표시하지 않습니다', body)

    def test_stale_ecb_is_not_reported_as_current(self):
        xml = b'<Cube><Cube time="2026-09-01"><Cube currency="KRW" rate="1500"/></Cube></Cube>'
        get = lambda *a, **k: SimpleNamespace(content=xml, raise_for_status=lambda: None)
        with self.assertRaises(ValueError):
            market.ecb_rates(get, datetime.datetime(2026, 10, 17))


if __name__ == '__main__':
    unittest.main()
