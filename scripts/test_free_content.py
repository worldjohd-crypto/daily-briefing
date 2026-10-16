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

    def test_news_dates_duplicates_and_urls(self):
        def item(title, link, date):
            return f'<item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate><source>방송사</source></item>'
        xml = '<rss><channel>' + item('새 기사', 'https://news.google.com/rss/articles/a', 'Fri, 16 Oct 2026 10:00:00 GMT') + item('옛 기사', 'https://news.google.com/rss/articles/b', 'Fri, 09 Oct 2026 10:00:00 GMT') + item('위험 링크', 'javascript:alert(1)', 'Fri, 16 Oct 2026 10:00:00 GMT') + '</channel></rss>'
        get = lambda *a, **k: SimpleNamespace(content=xml.encode())
        used = set()
        text = f.news_text(get, NOW, '연예', '연예', used)
        self.assertIn('새 기사', text)
        self.assertNotIn('옛 기사', text)
        self.assertNotIn('javascript:', text)
        self.assertIn('기사가 없습니다', f.news_text(get, NOW, '연예', '연예', used))

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
