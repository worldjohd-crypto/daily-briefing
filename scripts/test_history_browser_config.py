"""Meaningful offline regressions for the browser check's fail-closed policy."""
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

import check_history_browser as checker


class BrowserCheckPolicyTests(unittest.TestCase):
    def test_attribution_leaks_fail_but_article_detail_links_remain(self):
        article = "https://n.news.naver.com/mnews/article/001/0000000001"
        table = "https://official.example/table"
        source = "https://official.example/method"
        self.assertEqual(checker._copy_policy_errors("기사 제목\n" + article, [article],
                                                     [article, source], [table]), [])
        leaked = checker._copy_policy_errors("기사\n" + article + "\n자료: 공식 자료\n" + table + "\n" + source,
                                             [article], [source], [table])
        self.assertEqual(set(leaked), {"source_label_copied", "statistics_source_url_copied",
                                      "source_list_url_copied"})
        self.assertEqual(checker._copy_policy_errors("기사 제목만", [article], [], []),
                         ["article_detail_url_missing"])
        publisher_note = "과거 기사 표본은 당시 무료 보도로서 별도 편집 검토를 거칩니다."
        self.assertIn("source_description_copied", checker._copy_policy_errors(
            publisher_note, [], [], [], "출처: " + publisher_note))

    def test_forced_analogy_in_unrelated_fixture_is_rejected(self):
        with self.assertRaises(checker.CheckFailure) as caught:
            checker._assert_fixture_mode("현재 기사: 임원 부고\n유사점: 개발", "nomatch")
        self.assertEqual(caught.exception.code, "nomatch_forced_analogy")

    def test_both_offline_fixtures_use_real_cache_and_production_copy_code(self):
        repo = Path(__file__).resolve().parents[1]
        original_cache = (repo / "history-price-cache.json").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            serving = Path(tmp)
            fixtures, source_urls = checker._build_fixture_pages(repo, serving)
            self.assertEqual({item["mode"] for item in fixtures}, {"matched", "nomatch"})
            self.assertEqual(len(list(serving.iterdir())), 2)
            for fixture in fixtures:
                html = (serving / fixture["file"]).read_text(encoding="utf-8")
                soup = BeautifulSoup(html, "html.parser")
                box = soup.select_one("#historycompare")
                copied = box["data-copy-text"]
                checker._assert_fixture_mode(copied, fixture["mode"])
                self.assertIn("자동 점검용 테스트", soup.title.get_text())
                self.assertIn("navigator.clipboard.writeText(text)", html)
                self.assertIn("data-copy-text", html)
                self.assertTrue(all(url in copied for url in fixture["expectedDetailUrls"]))
                self.assertFalse(checker._copy_policy_errors(copied, fixture["expectedDetailUrls"],
                                                             source_urls, source_urls))
                source = soup.select_one(".source-note").get_text()
                self.assertTrue(all(url in source for url in source_urls))
        self.assertEqual((repo / "history-price-cache.json").read_bytes(), original_cache)


if __name__ == "__main__":
    unittest.main()
