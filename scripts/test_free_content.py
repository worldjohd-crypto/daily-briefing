import datetime
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import free_content as f

NOW = datetime.datetime(2026, 10, 16, 23, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

class FreeContentTests(unittest.TestCase):
    def test_upstream_freshness_and_json_only(self):
        page = '<h1>📅 2026년 10월 16일 금요일</h1><script>window.__content_daily19 = ' + json.dumps('오늘 OX') + ';</script>'
        get = lambda *a, **k: SimpleNamespace(text=page)
        self.assertIn('오늘 OX', f.source_sections(get, NOW)['daily19'])
        self.assertEqual(f.source_sections(get, NOW + datetime.timedelta(days=1)), {})

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
            with patch.object(f.requests, 'get', side_effect=AssertionError('no shortener calls')):
                result = f.prepare_news_text('heading\n\nTitle\n' + url, sid)
                self.assertIn('Title\n' + url, result)
                self.assertIn('href="' + url + '"', f.render_news_links(result))
                self.assertNotIn('da.gd', f.prepare_news_text('heading\n\nTitle\nhttps://da.gd/test', sid))

    def test_failures_preserve_ai(self):
        sections = [('ai_comment', '🤖', 'AI', 'cached commentary')]
        def fail(*a, **k):
            raise RuntimeError('offline')
        result = f.extend_sections(sections, fail, NOW)
        self.assertEqual(result[0], sections[0])
        self.assertEqual(len(result), 14)

    def test_default_page_never_calls_paid_api(self):
        import generate_briefing as g
        self.assertFalse(g.REFRESH_COMMENTS)
        with patch.object(g.requests, 'post', side_effect=AssertionError('paid API forbidden')):
            for key in ('politics', 'economy', 'realestate', 'world', 'finance', 'ai'):
                g._build_commentary(key, 'title', 'persona', 'prompt', ['headline'])

if __name__ == '__main__':
    unittest.main()
