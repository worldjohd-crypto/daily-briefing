"""Source integrity and outage tests for the free official Eum notice card."""

import datetime as dt
import unittest
from unittest.mock import Mock

import requests

import property_land as land


class LandUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 17, 11, 25, tzinfo=land.KST)

    def row(self, identity='643082', date='2026-10-16', href=None, title='[변경] 안성시 정비계획 결정 고시'):
        href = href or ('gvGosiDet.jsp?seq=' + identity + '&amp;pageNo=1')
        return (f'<tr><td>{date}</td><td>경기도 안성시 고시 제2026-305호</td>'
                f'<td><a href="{href}">{title}</a></td><td>경기도 안성시</td></tr>')

    def getter(self, rows, status=200, headings=None):
        headings = headings or ['고시일', '고시번호', '[구분]고시제목', '담당기관']
        html = ('<table><thead><tr>' + ''.join('<th>' + x + '</th>' for x in headings)
                + '</tr></thead><tbody>' + rows + '</tbody></table>')
        return Mock(return_value=Mock(status_code=status, text=html))

    def test_original_links_dates_agencies_and_deduplication(self):
        get = self.getter(self.row() + self.row()
                          + self.row('2', '2026-10-18') + self.row('3', '2026-09-17'))
        body = land.build_land_updates(get, self.now)
        self.assertEqual(body.count(land.EUM_DETAIL), 1)
        self.assertIn(land.EUM_DETAIL + '643082', body)
        self.assertIn('고시일 2026-10-16 · 경기도 안성시 고시 제2026-305호', body)
        self.assertIn('담당기관: 경기도 안성시', body)
        self.assertIn('개별 필지의 적용 규제나 건축 가능 여부를 판정한 결과가 아닙니다', body)
        self.assertEqual(get.call_args.kwargs['params'], {'startdt': '2026-09-18', 'enddt': '2026-10-17'})
        self.assertEqual(get.return_value.encoding, 'euc-kr')

    def test_first_page_sorted_by_date_and_limited_to_five(self):
        body = land.build_land_updates(self.getter(''.join(
            self.row(str(i), f'2026-10-{10 + i:02d}', title='고시 ' + str(i)) for i in range(7))), self.now)
        self.assertEqual(body.count(land.EUM_DETAIL), 5)
        self.assertNotIn('고시 0\n', body)
        self.assertLess(body.index('고시 6\n'), body.index('고시 2\n'))

    def test_empty_official_result_is_distinct_from_fetch_failure(self):
        body = land.build_land_updates(self.getter('<tr><td colspan="4">데이터가 없습니다.</td></tr>'), self.now)
        self.assertIn('해당하는 공식 고시가 없습니다', body)
        self.assertIn('수집: 2026-10-17 11:25', body)
        self.assertNotIn('확인하지 못했습니다', body)

    def test_malformed_links_and_dates_never_become_verified_notices(self):
        rows = [self.row(date='2026-02-30'), self.row(identity='javascript:bad'),
                self.row(href='https://evil.example/gvGosiDet.jsp?seq=2'),
                self.row(href='gvGosiDet.jsp?seq=2&amp;seq=3')]
        for row in rows:
            body = land.build_land_updates(self.getter(row), self.now)
            self.assertIn('확인하지 못했습니다', body)
            self.assertNotIn(land.EUM_DETAIL, body)
            self.assertNotIn('수집:', body)

    def test_schema_and_network_failure_are_visible(self):
        getters = [self.getter(self.row(), headings=['제목']), self.getter(self.row(), status=503),
                   Mock(side_effect=requests.ConnectionError('offline'))]
        for get in getters:
            body = land.build_land_updates(get, self.now)
            self.assertIn('이전 고시를 최신 정보로 표시하지 않습니다', body)
            self.assertIn('확인 시도: 2026-10-17 11:25', body)
            self.assertNotIn(land.EUM_DETAIL, body)

    def test_sources_are_footer_only_while_notice_links_stay_in_copy(self):
        from content_rendering import split_content_sources
        body = land.build_land_updates(self.getter(self.row()), self.now)
        copy, sources = split_content_sources(body)
        self.assertNotIn('자료:', copy)
        self.assertNotIn(land.EUM_LIST, copy)
        self.assertIn(land.EUM_DETAIL + '643082', copy)
        self.assertTrue(sources)

    def test_utc_near_midnight_is_converted_to_korean_calendar_date(self):
        get = self.getter(self.row())
        utc = dt.datetime(2026, 10, 16, 16, 0, tzinfo=dt.timezone.utc)
        body = land.build_land_updates(get, utc)
        self.assertEqual(get.call_args.kwargs['params']['enddt'], '2026-10-17')
        self.assertIn('수집: 2026-10-17 01:00', body)


if __name__ == '__main__':
    unittest.main()
