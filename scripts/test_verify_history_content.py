"""Meaningful offline mutations for the automatic housing-history gate."""

import copy
import datetime as dt
import html
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

import housing_history as hh
import verify_history_content as verifier
from content_rendering import render_history_links, render_sources, split_content_sources
from free_content import render_news_links


REPO = Path(__file__).resolve().parents[1]


def render_card(name, text):
    """Small HTML fixture; production browser-copy tests exercise the renderer."""
    body, sources = split_content_sources(text)
    if name == "historycompare":
        shown = render_history_links(body)
    elif name == "realestate":
        shown = render_news_links(body)
    else:
        shown = html.escape(body)
    footer = '<div class="source-note">' + render_sources(sources) + '</div>' if sources else ''
    return (f'<section><button class="copy-btn" onclick="copySection(\'{name}\')">복사</button>'
            f'<div id="{name}" data-copy-text="{html.escape(body, quote=True)}">{shown}</div>'
            + footer + '</section>')


class VerifyHistoryContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        self.collected = self.now.astimezone(verifier.KST)
        self.cache = verifier.read_json(REPO / "history-price-cache.json")
        self.cache["downloadedAt"] = (self.now - dt.timedelta(days=1)).isoformat()
        self.dataset = {**self.cache, "status": "cache", "stale": False,
                        "note": "한국부동산원 공식 API 직접 수집 자료 · 30일 이내 보관본"}
        self.catalog = verifier.read_json(REPO / "housing-history-articles.json")
        self.ai = {key: {"text": "# 유지된 논평 " + key + "\n\n검증용 기존 본문입니다.",
                         "updated": "기존 생성일"} for key in verifier.AI_KEYS}
        for name, value in (("history-price-cache.json", self.cache),
                            ("housing-history-articles.json", self.catalog),
                            ("comment-cache.json", self.ai)):
            self.write_json(name, value)
        self.write_page()

    def write_json(self, name, value):
        (self.repo / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def write_page(self, headline="택지 공급과 개발계획 발표", dataset=None):
        date = self.collected
        date_line = f"{date.year % 100}년 {date.month}월 {date.day}일 {verifier.WEEKDAYS[date.weekday()]}"
        news = ("🏠 [부동산 주요뉴스]\n" + date_line + "\n\n" + headline
                + "\nhttps://n.news.naver.com/mnews/article/001/0000000001")
        history = hh.build_history_comparison(news, None, self.collected,
                                             dataset=dataset or self.dataset,
                                             catalog_path=self.repo / "housing-history-articles.json")
        cards = [render_card("realestate", news), render_card("historycompare", history)]
        for key in verifier.AI_KEYS:
            cards.append(render_card(key + "_comment", date_line + " AI 논평 · "
                                     + self.ai[key]["updated"] + " 생성\n\n" + self.ai[key]["text"]))
        (self.repo / "briefing.html").write_text("<!doctype html><html><body>" + "".join(cards)
                                                + "</body></html>", encoding="utf-8")

    def page(self):
        return BeautifulSoup((self.repo / "briefing.html").read_text(encoding="utf-8"), "html.parser")

    def save_page(self, page):
        (self.repo / "briefing.html").write_text(str(page), encoding="utf-8")

    def verify(self, before=None, **kwargs):
        # No verifier branch may fetch data, even on malformed input or a stale
        # official cache.  Existing data are the only source for this gate.
        with patch("requests.get", side_effect=AssertionError("unexpected network access")), \
                patch.dict("os.environ", {"REFRESH_COMMENTS": "false"}):
            return verifier.verify_content(self.repo, ai_before=before, now=self.now,
                                           started_at=(self.now - dt.timedelta(minutes=1)).isoformat(), **kwargs)

    def assert_error(self, code, report=None):
        report = report or self.verify()
        self.assertEqual(report["status"], "failed")
        self.assertIn(code, [item["code"] for item in report["errors"]])

    def test_matching_articles_and_each_independent_horizon_pass(self):
        result = self.verify({"aiCache": copy.deepcopy(self.ai)})
        self.assertEqual(result["status"], "passed", result["errors"])
        self.assertEqual(result["summary"]["mode"], "current_headline_match")
        records = result["checks"]["content"]["articleLocalCalculations"]
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual([item["months"] for item in record["horizons"]], [6, 12, 24])
            for item in record["horizons"]:
                self.assertAlmostEqual(item["changePctUnrounded"], (item["index"] / record["baseIndex"] - 1) * 100)
        self.assertTrue(result["checks"]["ai"]["baselineCompared"])
        self.assertTrue(result["checks"]["articleMatching"])
        self.assertTrue(result["checks"]["prices"])
        self.assertTrue(result["checks"]["sourceSeparation"])
        self.assertTrue(result["checks"]["aiSnapshotCompared"])
        self.assertTrue(result["checks"]["aiCacheUnchanged"])
        self.assertFalse(result["summary"]["networkAccessed"])
        self.assertEqual(result["summary"]["actualBrowserClipboard"], "separate_gate_required")

    def test_unrelated_obituary_is_valid_reference_mode_not_false_match(self):
        self.write_page("택지 개발회사 대표 모친상")
        result = self.verify()
        self.assertEqual(result["status"], "passed", result["errors"])
        self.assertEqual(result["summary"]["mode"], "historical_reference_no_current_match")
        self.assertTrue(all(group["currentArticleUrl"] is None for group in result["checks"]["content"]["groups"]))

    def test_no_match_missing_reference_notice_fails(self):
        self.write_page("건설사 대표 모친상")
        page = self.page()
        box = page.find(id="historycompare")
        box["data-copy-text"] = box["data-copy-text"].replace("오늘 기사와의 유사 사례가 아닌", "같은 기사인")
        self.save_page(page)
        self.assert_error("NO_MATCH_REFERENCE_MODE")

    def test_swapping_article_price_blocks_fails_even_all_numbers_exist(self):
        page = self.page()
        box = page.find(id="historycompare")
        blocks = box["data-copy-text"].split("\n\n")
        indexes = [index for index, block in enumerate(blocks) if block.startswith("서울 아파트 매매가격지수")]
        self.assertEqual(len(indexes), 2)
        blocks[indexes[0]], blocks[indexes[1]] = blocks[indexes[1]], blocks[indexes[0]]
        box["data-copy-text"] = "\n\n".join(blocks)
        self.save_page(page)
        self.assert_error("ARTICLE_PRICE_CALCULATION")

    def test_percentage_sign_mutation_is_not_hidden_by_other_article(self):
        page = self.page()
        box = page.find(id="historycompare")
        copied = box["data-copy-text"]
        matched = re.search(r"(?m)^이후 6개월 \([^\n]+?\): ([+-])", copied)
        self.assertIsNotNone(matched)
        sign_at = matched.start(1)
        box["data-copy-text"] = copied[:sign_at] + ("-" if copied[sign_at] == "+" else "+") + copied[sign_at + 1:]
        self.save_page(page)
        self.assert_error("ARTICLE_PRICE_CALCULATION")

    def test_source_attribution_and_source_url_leaks_fail(self):
        original = (self.repo / "briefing.html").read_text(encoding="utf-8")
        for suffix, code in (("\n\n자료: 통계 출처", "COPY_SOURCE_LEAK"),
                             ("\n\n" + verifier.hp.TABLE_URL, "COPY_SOURCE_URL_LEAK")):
            with self.subTest(code=code):
                page = BeautifulSoup(original, "html.parser")
                box = page.find(id="historycompare")
                box["data-copy-text"] += suffix
                self.save_page(page)
                self.assert_error(code)

    def test_removing_screen_source_footer_fails(self):
        page = self.page()
        page.find(id="historycompare").find_parent("section").select_one(".source-note").decompose()
        self.save_page(page)
        self.assert_error("HISTORY_SOURCE_FOOTER_MISSING")

    def test_past_detail_link_missing_from_copy_fails(self):
        page = self.page()
        box = page.find(id="historycompare")
        past_link = next(link["href"] for link in box.select("a[href]") if "naver.com" not in link["href"])
        box["data-copy-text"] = box["data-copy-text"].replace(past_link, "")
        self.save_page(page)
        self.assert_error("HISTORICAL_ARTICLE_IDENTITY")

    def test_ai_cache_text_and_updated_mutations_fail(self):
        before = copy.deepcopy(self.ai)
        for field in ("text", "updated"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.ai)
                changed["realestate"][field] += " 바뀜"
                self.write_json("comment-cache.json", changed)
                self.assert_error("AI_CACHE_CHANGED", self.verify(before))

    def test_ai_html_mutation_fails_without_baseline(self):
        page = self.page()
        box = page.find(id="realestate_comment")
        box["data-copy-text"] = box["data-copy-text"].replace("검증용", "변경된")
        self.save_page(page)
        self.assert_error("AI_HTML_BODY_CHANGED")

    def test_ai_title_only_date_change_does_not_imply_regeneration(self):
        before = {"aiCache": copy.deepcopy(self.ai), "aiBodies": {}}
        page = self.page()
        for key in verifier.AI_KEYS:
            text = page.find(id=key + "_comment")["data-copy-text"]
            before["aiBodies"][key] = "어제 제목과 생성일\n\n" + text.split("\n\n", 1)[1]
        self.assertEqual(self.verify(before)["status"], "passed")

    def test_expired_official_cache_fails_with_previous_copy_preserved(self):
        self.cache["downloadedAt"] = (self.now - dt.timedelta(days=31)).isoformat()
        self.write_json("history-price-cache.json", self.cache)
        dataset = {**self.cache, "status": "cache", "stale": True,
                   "note": "공식 API 재확인 불가 · 아래 수집일의 이전 공식 자료 보관본 사용"}
        self.write_page(dataset=dataset)
        self.assert_error("OFFICIAL_CACHE_EXPIRED")

    def test_collection_date_weekday_is_validated(self):
        page = self.page()
        box = page.find(id="historycompare")
        box["data-copy-text"] = box["data-copy-text"].replace(verifier.WEEKDAYS[self.collected.weekday()], "잘못된요일", 1)
        box.clear()
        box.append(BeautifulSoup(render_history_links(box["data-copy-text"]), "html.parser"))
        self.save_page(page)
        self.assert_error("DATE_WEEKDAY_MISMATCH")

    def test_visible_price_mutation_cannot_hide_behind_correct_copy(self):
        page = self.page()
        box = page.find(id="historycompare")
        for node in box.find_all(string=True):
            if "이후 6개월" in str(node):
                node.replace_with(str(node).replace("이후 6개월", "이후 5개월", 1))
                break
        self.save_page(page)
        self.assert_error("VISIBLE_COPY_CONTENT_MISMATCH")

    def test_schedule_refresh_true_is_rejected_and_manual_no_baseline_is_supported(self):
        with patch.dict("os.environ", {"REFRESH_COMMENTS": "true"}):
            result = verifier.verify_content(self.repo, now=self.now, event="schedule")
        self.assert_error("SCHEDULE_AI_REFRESH_ENABLED", result)
        manual = self.verify(event="workflow_dispatch")
        self.assertEqual(manual["status"], "passed")
        self.assertTrue(manual["checks"]["aiCacheNonempty"])
        self.assertFalse(manual["checks"]["aiSnapshotCompared"])
        self.assertIsNone(manual["checks"]["aiCacheUnchanged"])

    def test_empty_comment_cache_is_rejected(self):
        self.ai["realestate"]["text"] = " "
        self.write_json("comment-cache.json", self.ai)
        self.assert_error("AI_CACHE_INCOMPLETE")

    def test_duplicate_json_and_error_text_never_echo_sensitive_input(self):
        path = self.repo / "history-price-cache.json"
        path.write_text('{"KEY=not-a-real-secret": 1, "KEY=not-a-real-secret": 2}', encoding="utf-8")
        result = self.verify()
        self.assert_error("JSON_DUPLICATE_KEY", result)
        self.assertNotIn("not-a-real-secret", json.dumps(result))

    def test_cli_writes_machine_readable_report_and_failure_exit(self):
        report = self.repo / "reports" / "result.json"
        # No run timestamp on purpose: this verifies the optional CLI contract.
        with patch.dict("os.environ", {"REFRESH_COMMENTS": "false"}):
            self.assertEqual(verifier.main(["--repo-dir", str(self.repo), "--html", str(self.repo / "briefing.html"),
                                            "--report", str(report)]), 0)
        self.assertEqual(verifier.read_json(report)["status"], "passed")
        (self.repo / "briefing.html").write_text("<html></html>", encoding="utf-8")
        self.assertEqual(verifier.main(["--repo-dir", str(self.repo), "--report", str(report)]), 1)
        self.assertEqual(verifier.read_json(report)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
