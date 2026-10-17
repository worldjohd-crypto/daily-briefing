"""No-network tests for live HUG field extraction and honest failure states."""

import datetime as dt
from html import escape
import unittest
from unittest.mock import Mock

import requests

from content_rendering import split_content_sources
import property_guarantee as hug


class GuaranteeTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 17, 4, tzinfo=dt.timezone.utc)
        self.notice = ('https://www.khug.or.kr/hug/web/cs/no/csno000001.jsp?'
                       'id=16&mode=S&currentPage=1&articleId=37801')

    def response(self, html, url=None, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = ('<meta charset="euc-kr">' + html).encode('euc-kr')
        response.url = url or hug.HUG_SOURCE
        return response

    def product(self, deposit='수도권 7억원 이하, 그 외 지역 5억원 이하', href=None):
        href = self.notice if href is None else href
        href = escape(href, quote=True)
        return self.response(f'''<h3>전세보증금반환보증</h3>
          <table><caption>전세보증금반환보증 상품 개요: 조건</caption>
          <tr><th>보증신청<br>기한</th><td>신규 전세계약은 잔금지급일과 전입신고일 중 늦은 날부터 전세계약기간의 1/2 경과 전</td></tr>
          <tr><th rowspan="2">보증대상 주택</th><td>아파트, 주거용 오피스텔, 다가구주택</td></tr>
          <tr><td>근린생활시설 등은 보증대상 주택이 아닙니다.</td></tr>
          <tr><th>보증조건</th><td><ul><li>전입신고와 확정일자 필요</li>
          <li>③ 전세보증금(월세가 있는 경우 전월세전환율 적용 전세보증금)이 {deposit}
          <a href="{href}">(※ 최신 전월세전환율 : 6.5% (’26.7.1. 이후 신규 및 보증금액이 증가하는 갱신보증 신청 건에 적용))← 클릭시 공지사항으로 이동</a></li></ul></td></tr>
          </table>''')

    def announcement(self, date='2026-06-24'):
        return self.response(f'''<ul><li class="tit"><strong>제목</strong><div>전세보증 관련 전월세전환율 변경 사전 안내</div></li>
          <li class="tit"><strong>작성일</strong><div>{date}</div></li></ul>''', self.notice)

    def test_current_page_values_and_rowspan_exclusions_are_fetched(self):
        get = Mock(side_effect=[self.product(), self.announcement()])
        body = hug.build_rental_guarantee(get, self.now)
        self.assertIn('수도권 7억원 이하', body)
        self.assertIn('근린생활시설 등은 보증대상 주택이 아닙니다', body)
        self.assertIn('6.5%', body)
        self.assertIn('공지 작성일 2026-06-24', body)
        self.assertIn('수집: 2026-10-17 13:00 (한국시간)', body)
        self.assertNotIn('← 클릭시', body)
        self.assertEqual([call.args[0] for call in get.call_args_list], [hug.HUG_SOURCE, self.notice])
        changed = hug.build_rental_guarantee(Mock(side_effect=[self.product('새 한도 8억원 이하로 변경'), self.announcement()]), self.now)
        self.assertIn('새 한도 8억원 이하로 변경', changed)
        self.assertNotIn('수도권 7억원 이하', changed)

    def test_source_footer_does_not_enter_copy_body(self):
        body = hug.build_rental_guarantee(Mock(side_effect=[self.product(), self.announcement()]), self.now)
        copied, sources = split_content_sources(body)
        self.assertIn('신청기한:', copied)
        self.assertIn('수집:', copied)
        self.assertNotIn('자료:', copied)
        self.assertNotIn('khug.or.kr', copied)
        self.assertIn(hug.HUG_SOURCE, str(sources))
        self.assertIn(self.notice, str(sources))

    def test_network_schema_and_partial_row_failures_are_visible(self):
        bad = [Mock(side_effect=requests.ConnectionError('offline')),
               Mock(return_value=self.response('점검중')),
               Mock(return_value=self.response('<table><caption>전세보증금반환보증 상품 개요</caption><tr><th>보증대상 주택</th><td>주택</td></tr></table>')),
               Mock(return_value=self.product()),
               Mock(return_value=self.response('forbidden', status=403))]
        # A truncated rowspan cannot silently omit a housing exclusion.
        broken = self.product()
        broken._content = broken.content.replace('rowspan="2"'.encode(), 'rowspan="3"'.encode())
        bad[3] = Mock(return_value=broken)
        for get in bad:
            with self.subTest(get=get):
                body = hug.build_rental_guarantee(get, self.now)
                self.assertIn('최신 HUG 전세보증금반환보증 가입 요건을 확인하지 못했습니다', body)
                self.assertNotIn('수도권 7억원', body)
                self.assertNotIn('\n\n수집:', body)

    def test_unsafe_notice_link_is_not_requested_or_exposed(self):
        for href in ('https://example.com/?articleId=1', 'javascript:alert(1)',
                     'https://www.khug.or.kr/hug/web/cs/no/csno000001.jsp?id=16&mode=S&articleId=bad',
                     'https://www.khug.or.kr:9443/hug/web/cs/no/csno000001.jsp?id=16&mode=S&articleId=1'):
            get = Mock(return_value=self.product(href=href))
            body = hug.build_rental_guarantee(get, self.now)
            self.assertEqual(get.call_count, 1)
            self.assertNotIn(href, body)
            self.assertIn('연결된 전월세전환율 공지를 확인하지 못했습니다', body)

    def test_optional_notice_failure_keeps_verified_product_without_fake_date(self):
        for result in (requests.Timeout('offline'), self.announcement('2026-10-18')):
            get = Mock(side_effect=[self.product(), result])
            body = hug.build_rental_guarantee(get, self.now)
            self.assertIn('수도권 7억원 이하', body)
            self.assertIn('공지의 제목·작성일은 확인하지 못했습니다', body)
            self.assertNotIn('공지 작성일 2026-10-18', body)
            self.assertNotIn('최신 HUG 전세보증금반환보증 가입 요건을 확인하지 못했습니다', body)


if __name__ == '__main__':
    unittest.main()
