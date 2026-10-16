"""Public notice integrity tests, with no network, account or paid client."""

import datetime as dt
import unittest
from unittest.mock import Mock

import requests

import property_resources as resources


class PropertyResourcesTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 17, 2, tzinfo=resources.KST)

    def notice(self, identity='21428', **changes):
        row = dict(pblancId=identity, prgrStts='모집중', useAt='Y',
                   rcritPblancDe='20261016', pblancNm='제주 든든전세 입주자 모집',
                   suplyInsttNm='LH', mhshldBrtcCodeNm='제주특별자치도', sttusSe='1')
        row.update(changes)
        return row

    def getter(self, rows, total=None):
        return Mock(return_value=Mock(json=lambda: {'resultList': rows, 'resultCnt': len(rows) if total is None else total}))

    def test_active_official_notices_only_with_real_date_and_identity(self):
        rows = [self.notice(), self.notice(),
                self.notice('1', prgrStts='모집완료'),
                self.notice('2', rcritPblancDe='20261018'),
                self.notice('3', rcritPblancDe='20260831'),
                self.notice('4', useAt='N'), self.notice('5', sttusSe='2')]
        get = self.getter(rows)
        body = resources.build_public_rental(get, self.now)
        self.assertEqual(body.count(resources.MYHOME_DETAIL), 2)
        self.assertIn('공고일 2026-10-16 · LH · 제주특별자치도', body)
        self.assertIn('[정정공고]', body)
        self.assertIn('오늘 접수 가능하다는 뜻과 다를 수 있습니다', body)
        self.assertEqual(get.call_args.kwargs['params']['srchRcritPblancDeYearMtBegin'], '202609')
        self.assertEqual(get.call_args.kwargs['params']['srchPrgrStts'], '1')

    def test_month_rollover_and_year_boundary(self):
        now = dt.datetime(2027, 1, 1, 0, 1, tzinfo=resources.KST)
        get = self.getter([self.notice(rcritPblancDe='20261231')])
        body = resources.build_public_rental(get, now)
        self.assertIn('2026-12 ~ 2027-01', body)
        self.assertEqual(get.call_args.kwargs['params']['srchRcritPblancDeYearMtBegin'], '202612')

    def test_malformed_results_do_not_look_like_zero_or_verified_notices(self):
        bad_rows = [self.notice(pblancId='javascript:bad'), self.notice(rcritPblancDe='20260230'),
                    self.notice(pblancNm=None)]
        getters = [self.getter([row]) for row in bad_rows]
        getters += [self.getter([], total=3), Mock(side_effect=requests.ConnectionError('offline'))]
        for get in getters:
            body = resources.build_public_rental(get, self.now)
            self.assertIn('최신 공식 모집공고 목록을 확인하지 못했습니다', body)
            self.assertNotIn('해당하는 공식 모집공고가 없습니다', body)
            self.assertNotIn(resources.MYHOME_DETAIL, body)

    def test_empty_source_is_explicit_and_guides_survive_outage(self):
        body = resources.build_public_rental(self.getter([]), self.now)
        self.assertIn('해당하는 공식 모집공고가 없습니다', body)
        cards = resources.build_property_resources(Mock(side_effect=requests.ConnectionError('offline')), self.now)
        self.assertEqual(len(cards), 4)
        self.assertEqual(len({card[0] for card in cards}), 4)
        for _, _, _, text in cards:
            self.assertIn('\n\n자료:', text)
        self.assertIn('상시 확인 가이드', cards[1][3])


if __name__ == '__main__':
    unittest.main()
