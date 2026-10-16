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
        return Mock(return_value=Mock(status_code=200, json=lambda: {'resultList': rows, 'resultCnt': len(rows) if total is None else total}))

    def lh_row(self, identity='2015122300020922', title='LH 주택 공고', status='공고중',
               published='2026.10.16', closing='2026.10.28', kind='매입임대', codes=('13', '26')):
        return (f'<tr><td>1</td><td>{kind}</td><td>'
                f'<a class="wrtancInfoBtn" data-id1="{identity}" data-id2="03" '
                f'data-id3="{codes[0]}" data-id4="{codes[1]}"><span>{title}<em class="day">1일전</em></span></a>'
                f'</td><td>제주특별자치도</td><td>첨부</td><td>{published}</td><td>{closing}</td>'
                f'<td>{status}</td><td>100</td></tr>')

    def lh_response(self, rows):
        headings = ''.join(f'<th>{word}</th>' for word in ['번호', '유형', '공고명', '지역', '첨부', '게시일', '마감일', '상태', '조회수'])
        return Mock(status_code=200, text=f'<div class="bbs_ListA"><table><thead><tr>{headings}</tr></thead><tbody>{rows}</tbody></table></div>')

    def fallback_getter(self, rows):
        return Mock(side_effect=[Mock(status_code=903), self.lh_response(rows)])

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

    def test_official_lh_html_recovers_myhome_failure_and_identifies_actual_scope(self):
        rows = ''.join(self.lh_row(identity=str(2015122300020922 + index),
                                   status='접수중' if index == 1 else '공고중') for index in range(6))
        get = self.fallback_getter(rows)
        body = resources.build_public_rental(get, self.now)
        self.assertEqual(body.count(resources.LH_DETAIL), 5)
        self.assertIn('LH 임대주택 공급 공고', body)
        self.assertIn('마감일 2026-10-28', body)
        self.assertIn(' · 접수중', body)
        self.assertIn(' · 공고중', body)
        self.assertNotIn('1일전', body)
        self.assertNotIn(resources.MYHOME_SOURCE, body)
        self.assertIn('자료: LH 한국토지주택공사', body)
        self.assertEqual(get.call_args.args[0], resources.LH_SOURCE)

    def test_lh_excludes_nonhousing_closed_stale_future_and_bad_identity_rows(self):
        bad = [self.lh_row(status='접수마감'), self.lh_row(published='2026.08.31'),
               self.lh_row(published='2026.10.18'), self.lh_row(closing='2026.10.16'),
               self.lh_row(identity='javascript:bad'), self.lh_row(closing='2026.02.30'),
               self.lh_row(kind='임대상가(입찰)', codes=('22', '43')),
               self.lh_row(kind='분양주택', codes=('05', '05')),
               self.lh_row(kind='가정어린이집', codes=('06', '40')),
               self.lh_row(kind='국민임대', codes=('22', '43'))]
        good = self.lh_row(title='정상 주택 공고', status='정정공고중', closing='2026.10.17')
        body = resources.build_public_rental(self.fallback_getter(''.join(bad) + good), self.now)
        self.assertEqual(body.count(resources.LH_DETAIL), 1)
        self.assertIn('정상 주택 공고', body)
        self.assertIn(' · 정정공고중', body)
        self.assertNotIn('임대상가', body)
        self.assertNotIn('javascript:', body)

    def test_lh_duplicate_and_schema_errors_do_not_invent_availability(self):
        row = self.lh_row()
        body = resources.build_public_rental(self.fallback_getter(row + row), self.now)
        self.assertEqual(body.count(resources.LH_DETAIL), 1)
        for response in [Mock(status_code=403), Mock(status_code=200, text='<html>점검중</html>'), self.lh_response('')]:
            get = Mock(side_effect=[requests.ConnectionError('offline'), response])
            body = resources.build_public_rental(get, self.now)
            self.assertIn('최신 공식 모집공고 목록을 확인하지 못했습니다', body)
            self.assertNotIn(resources.LH_DETAIL, body)


if __name__ == '__main__':
    unittest.main()
