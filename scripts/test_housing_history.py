import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import history_prices as hp
import housing_history as hh
from content_rendering import split_content_sources
from test_history_prices import fake_official_get

NOW = dt.datetime(2026, 10, 17, 20, tzinfo=dt.timezone(dt.timedelta(hours=9)))
CURRENT = "🏠 [부동산 주요뉴스]\n26년 10월 17일 토요일\n\n성수 부지 개발 시동\nhttps://n.news.naver.com/mnews/article/648/12345"


class HousingHistoryTests(unittest.TestCase):
    def test_both_eras_with_real_months_formula_and_source_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = hp.load_history_prices(get=fake_official_get(), key='test-key',
                                            cache_path=Path(tmp) / 'p.json', now=NOW)
        body = hh.build_history_comparison(CURRENT, lambda *a, **k: self.fail('news fetched twice'),
                                           NOW, dataset=dataset)
        content, sources = split_content_sources(body)
        self.assertIn('현재 기사: 성수 부지 개발 시동', content)
        self.assertIn('노무현 정부', content)
        self.assertIn('문재인 정부', content)
        self.assertIn('공공택지', content)
        self.assertIn('차이점:', content)
        for date in ('2006-11-15', '2018-09-21'):
            result = hp.compare_price_changes(dataset, date)
            for h in result['horizons'].values():
                self.assertIn(f"{h['change_pct']:+.2f}%", content)
                self.assertIn(h['month'][:4] + '-' + h['month'][4:], content)
        self.assertNotIn('확인하지 못했습니다', content)
        self.assertIn('https://news.sbs.co.kr/news/endPage.do?', content)
        self.assertNotIn('자료:', content)
        self.assertNotIn(hp.TABLE_URL, content)
        self.assertIn(hp.TABLE_URL, sources)
        self.assertNotIn('SBS', content)
        self.assertIn('SBS', sources)

    def test_unrelated_obituary_does_not_force_similarity(self):
        news = '건설사 전무 모친상\nhttps://n.news.naver.com/mnews/article/001/1234'
        with patch.object(hh, 'load_history_prices', return_value={'status': 'unavailable', 'series': {}}):
            body = hh.build_history_comparison(news, None, NOW)
        self.assertNotIn('유사점:', body)
        self.assertIn('유사점(두 과거 기사):', body)
        self.assertIn('유사 사례가 아닌', body)
        self.assertIn('수치를 추정하지 않습니다', body)
        self.assertNotRegex(body, r'[+-]\d+\.\d+%')

    def test_no_wrong_topic_or_duplicate_matches(self):
        catalog = json.loads(hh.CATALOG.read_text(encoding='utf-8'))['articles']
        news = [{'title': 'DSR 대출 규제', 'url': 'a'}, {'title': '대출 금리 변화', 'url': 'b'},
                {'title': '전세난 확산', 'url': 'c'}, {'title': '신도시 택지 공급', 'url': 'd'}]
        groups = hh.select_comparisons(news, catalog)
        self.assertEqual([x['topic'] for x in groups], ['credit', 'jeonse'])
        self.assertEqual([a['era'] for a in groups[0]['references']], list(hh.ERAS))
        self.assertEqual([a['era'] for a in groups[1]['references']], ['문재인 정부'])
        rebuilt = hh.select_comparisons([{'title': '재건축 개발이익 부담금', 'url': 'e'}], catalog)
        self.assertEqual(rebuilt[0]['topic'], 'rebuild')

    def test_existing_news_and_ai_remain_unchanged_and_separate(self):
        import free_content as f
        sections = [('realestate', '🏠', '부동산 주요뉴스', CURRENT),
                    ('realestate_comment', '🧠', 'AI 논평', 'keep cached text')]
        with patch.object(hh, 'build_history_comparison', return_value='free comparison') as build, \
                patch('housing_story.build_story_section', return_value='free story') as story, \
                patch('official_guides.build_guides', return_value={}), \
                patch('official_metrics.build_cryptofear', return_value=None), \
                patch('official_metrics.build_wikiinterest', return_value=None), \
                patch('official_housing.build_transactions', return_value=None), \
                patch('official_housing.build_seoulcommerce', return_value=None), \
                patch('property_resources.build_property_resources', return_value=[]):
            result = f.extend_sections(sections.copy(), lambda *a, **k: (_ for _ in ()).throw(RuntimeError()), NOW)
        self.assertEqual(result[0], sections[0])
        self.assertEqual(result[1], ('historycompare', '🕰️', '과거 상승기 뉴스·집값 비교', 'free comparison'))
        self.assertEqual(result[2], ('housingstory', '📚', '실거래로 읽는 투자 이야기', 'free story'))
        self.assertEqual(result[3], sections[1])
        build.assert_called_once_with(CURRENT, unittest.mock.ANY, NOW)
        story.assert_called_once_with(CURRENT, unittest.mock.ANY, NOW)
        self.assertNotIn('historycompare', f.NEWS_HEADINGS)


if __name__ == '__main__':
    unittest.main()
