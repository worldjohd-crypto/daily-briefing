import datetime
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import free_content as f

NOW = datetime.datetime(2026, 10, 16, 23, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

class FreeContentTests(unittest.TestCase):
    def test_naver_entertainment_and_topic_selection(self):
        items = [{'title': title, 'url': 'https://m.entertain.naver.com/now/article/001/' + str(1000+i), 'subContent': ''} for i, title in enumerate(['배우 결혼 발표', '예능 방송 출연', '가수 신곡 발표'])]
        items.append({'title':'외부 기사', 'url':'https://da.gd/test'})
        get = lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'success':True,'result':{'newsList':items}})
        with patch.object(f, '_NAVER_ENTERTAINMENT', None):
            used = set()
            romance = f.news_text(get, NOW, '열애·결혼 소식', '', used)
            broadcast = f.news_text(get, NOW, '방송 화제', '', used)
            general = f.news_text(get, NOW, '연예 뉴스', '', used)
            self.assertIn('배우 결혼 발표', romance)
            self.assertIn('예능 방송 출연', broadcast)
            self.assertIn('가수 신곡 발표', general)
            self.assertNotIn('da.gd', romance + broadcast + general)

    def test_all_news_original_naver_urls_no_shortener(self):
        url = 'https://n.news.naver.com/mnews/article/001/123'
        for sid in f.NEWS_HEADINGS:
            with patch('requests.get', side_effect=AssertionError('no shortener calls')):
                result = f.prepare_news_text('heading\n\nTitle\n' + url, sid)
                self.assertIn('Title\n' + url, result)
                self.assertIn('href="' + url + '"', f.render_news_links(result))
                self.assertNotIn('da.gd', f.prepare_news_text('heading\n\nTitle\nhttps://da.gd/test', sid))

    def test_direct_extras_do_not_replace_base_content_or_use_mirror(self):
        sections = [('ai_comment', '🤖', 'AI', 'cached commentary'), ('fuelfx', '⛽', 'FX', 'direct rates')]
        def fail(*a, **k):
            raise RuntimeError('offline')
        with patch('official_guides.build_guides', return_value={'daily19': 'official guide'}), patch('official_metrics.build_cryptofear', return_value='official index'), patch('official_metrics.build_wikiinterest', return_value='official views'), patch('official_housing.build_transactions', return_value=None), patch('official_housing.build_seoulcommerce', return_value=None):
            result = f.extend_sections(sections.copy(), fail, NOW)
        self.assertEqual(result[:2], sections)
        ids = [x[0] for x in result]
        self.assertNotIn('transactions', ids)
        self.assertNotIn('seoulcommerce', ids)
        self.assertIn('cryptofear', ids)
        self.assertNotIn('busanfransisco-source', str(result))

    def test_default_page_never_calls_paid_api(self):
        import generate_briefing as g
        self.assertFalse(g.REFRESH_COMMENTS)
        with patch.object(g.requests, 'post', side_effect=AssertionError('paid API forbidden')):
            for key in ('politics', 'economy', 'realestate', 'world', 'finance', 'ai'):
                g._build_commentary(key, 'title', 'persona', 'prompt', ['headline'])

if __name__ == '__main__':
    unittest.main()
