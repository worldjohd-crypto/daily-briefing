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

    def test_portal_dates_duplicates_adwall_and_redirects(self):
        valid = 'https://v.daum.net/v/20261016100000000'
        old = 'https://v.daum.net/v/20261009100000000'
        gated = 'https://v.daum.net/v/20261016110000000'
        redirected = 'https://v.daum.net/v/20261016120000000'
        listing = ''.join(f'<a href="{url}"><strong class="tit_txt">{title}</strong></a>' for title, url in [('정상 기사', valid), ('오래된 기사', old), ('광고 유도', gated), ('언론사 재연결', redirected), ('지원하지 않는 주소', 'https://ads.example/a')])
        article = '<meta property="og:title" content="정상 기사"><article>' + '본문 내용입니다. ' * 30 + '</article>'
        def get(url, **kwargs):
            if url == 'https://entertain.daum.net/news':
                return SimpleNamespace(text=listing)
            if url == gated:
                return SimpleNamespace(text=article + '계속하려면 광고를 시청하세요', url=url)
            return SimpleNamespace(text=article, url='https://publisher.example/article' if url == redirected else url)
        with patch.object(f, '_PORTAL_CANDIDATES', None), patch.object(f, '_VERIFIED_PORTAL_ARTICLES', {}), patch.object(f, 'NEWS_SOURCE_AUDIT_FILE') as audit:
            audit.read_text.return_value = '{}'
            used = set()
            text = f.news_text(get, NOW, '연예', 'query', used)
            self.assertIn('정상 기사', text)
            self.assertNotIn(old, text)
            self.assertNotIn(gated, text)
            self.assertNotIn(redirected, text)
            self.assertIn('기사가 없습니다', f.news_text(get, NOW, '연예', 'query', used))

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
