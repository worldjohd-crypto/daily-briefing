import unittest

from content_rendering import render_notice_links, render_sources, split_content_sources


class ContentRenderingTests(unittest.TestCase):
    def test_official_notice_title_is_clickable_and_copy_keeps_original_url(self):
        body = '모집공고\n공고일 2026-10-17\nhttps://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcDetailView.do?pblancId=123'
        rendered = render_notice_links(body)
        self.assertIn('>모집공고</a>', rendered)
        self.assertIn('공고일 2026-10-17', rendered)
        self.assertIn('pblancId=123', body)
        self.assertNotIn('<a', render_notice_links('bad\njavascript:alert(1)'))
        self.assertIn('>LH 공고</a>', render_notice_links('LH 공고\n게시일 2026-10-17\nhttps://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?panId=123'))
        self.assertIn('>도시계획 변경 고시</a>', render_notice_links('도시계획 변경 고시\n고시일 2026-10-16\nhttps://www.eum.go.kr/web/gs/gv/gvGosiDet.jsp?seq=643082'))
        self.assertNotIn('<a', render_notice_links('외부 링크\nhttps://www.eum.go.kr.evil.example/web/gs/gv/gvGosiDet.jsp?seq=1'))

    def test_live_property_cards_keep_reading_dates_but_exclude_source_footers(self):
        from bs4 import BeautifulSoup
        from generate_briefing import render_section
        for sid in ('rentalguarantee', 'housingfinance', 'landcheck'):
            body = ('공시일: 2026-10-01\n\n공식 자료에서 확인한 내용\n\n'
                    '고시 제목\n고시일 2026-10-16\nhttps://www.eum.go.kr/web/gs/gv/gvGosiDet.jsp?seq=643082\n\n'
                    '수집: 2026-10-17 10:00 (한국시간)\n\n자료: 공식 기관\nhttps://www.hf.go.kr/ko/sub01/sub01_01_04.do')
            page = BeautifulSoup(render_section(sid, '🏠', '부동산 정보', body), 'html.parser')
            copied = page.find(id=sid)['data-copy-text']
            self.assertIn('공시일: 2026-10-01', copied)
            self.assertIn('수집: 2026-10-17 10:00', copied)
            self.assertIn('gvGosiDet.jsp?seq=643082', copied)
            self.assertNotIn('자료:', copied)
            self.assertNotIn('hf.go.kr', copied)
            self.assertIn('공식 기관', page.select_one('.source-note').get_text())

    def test_middle_and_trailing_sources_removed_but_reading_dates_and_news_links_stay(self):
        text = ('기준일: 2026-10-17\n\n63/100\n\n자료 : Alternative.me\n'
                'https://alternative.me/\n\n전일 53\n\n기사 제목\n'
                'https://n.news.naver.com/mnews/article/001/123\n\n'
                '공식 출처: 법제처\nhttps://www.law.go.kr/\n'
                '원자료: 상시 안내\n편집 검토: 2026-10-17')
        body, sources = split_content_sources(text)
        self.assertIn('기준일: 2026-10-17', body)
        self.assertIn('전일 53', body)
        self.assertIn('https://n.news.naver.com/', body)
        self.assertNotIn('Alternative.me', body)
        self.assertNotIn('law.go.kr', body)
        self.assertNotIn('편집 검토', body)
        self.assertIn('Alternative.me', sources)
        self.assertIn('law.go.kr', sources)

    def test_source_html_cannot_execute_and_dates_are_not_source_labels(self):
        body, sources = split_content_sources('자료 기준: 02:00\n본문\n출처: <script>x</script>\nhttps://a.example/?x=1&y=2')
        self.assertIn('자료 기준: 02:00', body)
        rendered = render_sources(sources)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('x=1&amp;y=2', rendered)

    def test_card_copy_contains_body_only_and_attribution_remains_visible_below(self):
        from bs4 import BeautifulSoup
        from generate_briefing import render_section
        page = BeautifulSoup(render_section('cryptofear', '📊', '지수',
                             '지수 63\n\n자료: Alternative.me\nhttps://alternative.me/\n\n전일 53'), 'html.parser')
        box = page.find(id='cryptofear')
        self.assertIn('전일 53', box['data-copy-text'])
        self.assertNotIn('Alternative.me', box['data-copy-text'])
        self.assertNotIn('alternative.me', box.get_text())
        self.assertIn('Alternative.me', page.select_one('.source-note').get_text())
        self.assertEqual(page.select_one('.source-note a')['href'], 'https://alternative.me/')


if __name__ == '__main__':
    unittest.main()
