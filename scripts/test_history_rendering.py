import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from content_rendering import render_history_links, split_content_sources


class HistoryRenderingTests(unittest.TestCase):
    def test_article_pairs_link_titles_without_losing_comparison_lines(self):
        urls = [
            'https://n.news.naver.com/mnews/article/001/12345',
            'https://www.edaily.co.kr/News/Read?newsId=123&mediaCodeNo=257',
            'https://www.seoul.co.kr/news/economy/2005/08/31/20050831001001',
            'https://www.korea.kr/news/policyNewsView.do?newsId=123',
            'https://www.donga.com/news/Economy/article/all/20060330/123/1',
            'https://news.sbs.co.kr/news/endPage.do?news_id=123',
        ]
        body = ('현재 기사\n' + urls[0] + '\n\n'
                '6개월 후 +1.00% · 1년 후 -0.50%\n\n'
                + '\n\n'.join(f'과거 기사 {index}\n{url}' for index, url in enumerate(urls[1:])))
        page = BeautifulSoup(render_history_links(body), 'html.parser')
        self.assertEqual([link['href'] for link in page.select('a')], urls)
        self.assertEqual(page.select_one('a').get_text(), '현재 기사')
        self.assertIn('6개월 후 +1.00% · 1년 후 -0.50%', page.get_text())
        self.assertNotIn(urls[0], page.get_text())

    def test_unapproved_schemes_hosts_and_paths_remain_plain_text(self):
        for url in (
            'javascript:alert(1)',
            'http://www.korea.kr/news/policyNewsView.do?newsId=123',
            'https://www.korea.kr.evil.example/news/123',
            'https://www.edaily.co.kr.evil.example/News/Read?newsId=123',
            'https://www.edaily.co.kr/other?newsId=123',
            'https://www.seoul.co.kr/other/123',
            'https://www.donga.com/news/Entertainment/article/all/123',
            'https://www.donga.com/news/Economy/article/all/',
            'https://news.sbs.co.kr/news/other.do?news_id=123',
            'https://news.naver.com/breakingnews/section/101/260',
            'https://www.korea.kr:444/news/123',
            'https://untrusted@www.korea.kr/news/123',
            'https://www.korea.kr/news/123 trailing text',
            'https://[malformed/news/123',
        ):
            with self.subTest(url=url):
                rendered = render_history_links('기사 제목\n' + url)
                self.assertNotIn('<a ', rendered)
                self.assertIn('기사 제목', rendered)

    def test_source_html_and_headline_html_cannot_execute(self):
        body = '<img src=x onerror=alert(1)>\nhttps://www.korea.kr/news/a?x=1&y=%22test%22'
        rendered = render_history_links(body)
        self.assertNotIn('<img ', rendered)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', rendered)
        self.assertIn('x=1&amp;y=%22test%22', rendered)

    def test_history_card_keeps_original_copy_and_separate_source_footer(self):
        from generate_briefing import render_section

        current = 'https://n.news.naver.com/mnews/article/001/12345'
        historical = 'https://www.edaily.co.kr/News/Read?newsId=123&mediaCodeNo=257'
        source = 'https://www.reb.or.kr/r-one/portal/openapi/openApiDevPage.do'
        original = ('현재 기사\n' + current + '\n\n과거 기사\n' + historical
                    + '\n\n기사 기준월 2005-08 · 6개월 후 +1.00%\n\n'
                    '자료: 한국부동산원 월간 지수\n' + source)
        # Rendering a free card must never enter the commentary builder.
        with patch('generate_briefing._ask_claude', side_effect=AssertionError('Paid AI forbidden')):
            page = BeautifulSoup(render_section('historycompare', '🕰️', '과거와 현재 비교', original), 'html.parser')
        box = page.find(id='historycompare')
        self.assertEqual(box['data-copy-text'], split_content_sources(original)[0])
        self.assertIn(current, box['data-copy-text'])
        self.assertIn(historical, box['data-copy-text'])
        self.assertNotIn('자료:', box['data-copy-text'])
        self.assertNotIn(source, box['data-copy-text'])
        self.assertEqual(len(box.select('a')), 2)
        self.assertIn('한국부동산원 월간 지수', page.select_one('.source-note').get_text())
        self.assertEqual(page.select_one('.source-note a')['href'], source)

    def test_other_free_cards_keep_their_plain_text_rendering(self):
        from generate_briefing import render_section

        body = '기사 제목\nhttps://www.korea.kr/news/policyNewsView.do?newsId=123'
        page = BeautifulSoup(render_section('rentalguarantee', '🛡️', '전세보증', body), 'html.parser')
        self.assertEqual(page.find(id='rentalguarantee')['data-copy-text'], body)
        self.assertIsNone(page.find(id='rentalguarantee').find('a'))


if __name__ == '__main__':
    unittest.main()
