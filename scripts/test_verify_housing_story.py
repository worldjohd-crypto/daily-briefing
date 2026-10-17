"""Offline mutations for story provenance, cash accounting and attribution."""

import copy
import datetime as dt
import hashlib
import html
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

import housing_story as story
import verify_housing_story as verifier
from content_rendering import render_history_links, render_sources, split_content_sources


REPO = Path(__file__).resolve().parents[1]


def render_card(name, content):
    body, sources = split_content_sources(content)
    rendered = render_history_links(body)
    footer = '<div class="source-note">' + render_sources(sources) + '</div>' if sources else ''
    return (f'<section><button class="copy-btn" onclick="copySection(\'{name}\')">복사</button>'
            + f'<div id="{name}" data-copy-text="{html.escape(body, quote=True)}">{rendered}</div>'
            + footer + '</section>')


class VerifyHousingStoryTests(unittest.TestCase):
    def setUp(self):
        test_root = REPO / "reports"
        test_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="story-unit-", dir=test_root)
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.now = dt.datetime.now(verifier.KST).replace(second=0, microsecond=0)
        self.catalog = verifier.read_json(REPO / "housing-history-articles.json")
        self.write_json("housing-history-articles.json", self.catalog)
        # This is an explicitly synthetic unit-test CSV, never a published
        # official observation. Test data cannot trigger source downloads.
        csv = ("시군구,단지명,전용면적(㎡),계약년월,계약일,거래금액(만원),층,해제사유발생일\n"
               "테스트 서울구,검증용 아파트,84.9,201708,29,99000,21,-\n"
               "테스트 서울구,검증용 아파트,84.9,201908,1,142000,17,-\n")
        path = self.repo / "housing-story-evidence" / "fixture.csv"
        path.parent.mkdir()
        path.write_bytes(csv.encode("utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records = [{"date": date, "address": "테스트 서울구", "apartment": "검증용 아파트",
                    "areaSqm": "84.9", "priceManwon": price, "floor": floor, "cancelled": False,
                    "sourceUrl": story.PAGE, "evidenceFile": "housing-story-evidence/fixture.csv",
                    "evidenceSha256": digest}
                   for date, price, floor in (("2017-08-29", 99000, 21), ("2019-08-01", 142000, 17))]
        self.data = {"schemaVersion": 1, "reviewedAt": (self.now - dt.timedelta(days=1)).isoformat(),
                     "source": {"provider": "공식형식 테스트 원본", "url": story.PAGE,
                                "method": "명확히 표시한 오프라인 합성 fixture"},
                     "cases": [{"id": "unit-fixture", "location": "테스트 서울구", "apartment": "검증용 아파트",
                                "areaSqm": "84.9", "selectionMethod": "오프라인 테스트용 합성 계약",
                                "topics": ["credit"], "historicalArticleIds": ["moon-credit-20170802"],
                                "transactions": records}]}
        self.write_json("housing-story-evidence.json", self.data)
        self.write_page()

    def write_json(self, name, value):
        (self.repo / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def write_page(self, headline="주택 대출 규제 강화"):
        self.news = ("🏠 [부동산 주요뉴스]\n" + f"{self.now.year % 100}년 {self.now.month}월 {self.now.day}일 "
                     + verifier.WEEKDAYS[self.now.weekday()] + "\n\n" + headline
                     + "\nhttps://n.news.naver.com/mnews/article/001/0000000001")
        section = story.build_story_section(self.news, verifier._no_network, self.now,
                                            catalog_path=self.repo / "housing-history-articles.json",
                                            evidence_path=self.repo / "housing-story-evidence.json",
                                            evidence_base_dir=self.repo)
        (self.repo / "briefing.html").write_text('<html><body>' + render_card("realestate", self.news)
                                               + render_card("housingstory", section) + '</body></html>', encoding="utf-8")

    def page(self):
        return BeautifulSoup((self.repo / "briefing.html").read_text(encoding="utf-8"), "html.parser")

    def save(self, page):
        (self.repo / "briefing.html").write_text(str(page), encoding="utf-8")

    def add_timeline_fixture(self):
        header = "시군구,단지명,전용면적(㎡),계약년월,계약일,거래금액(만원),층,해제사유발생일\n"
        rows = {2017: "테스트 서울구,검증용 아파트,84.9,201708,29,99000,21,-\n"
                      "테스트 서울구,검증용 아파트,84.9,201708,30,100000,22,-\n",
                2018: "",
                2019: "테스트 서울구,검증용 아파트,84.9,201908,1,142000,17,-\n"}
        self.data["evidenceExports"] = []
        for year, values in rows.items():
            path = self.repo / "housing-story-evidence" / f"year-{year}.csv"
            raw = (header + values).encode("utf-8")
            path.write_bytes(raw)
            self.data["evidenceExports"].append({"file": f"housing-story-evidence/year-{year}.csv",
                                                "year": year, "sha256": hashlib.sha256(raw).hexdigest(),
                                                "bytes": len(raw), "validRecords": len(values.splitlines())})
        case = self.data["cases"][0]
        case["policyDate"] = "2017-08-02"
        case["timeline"] = [{"monthOffset": 0, "countInMonth": 2, "record": copy.deepcopy(case["transactions"][0])},
                            {"monthOffset": 6, "countInMonth": 0, "record": None,
                             "status": "no_same_area_transaction_in_official_export"},
                            {"monthOffset": 12, "countInMonth": 0, "record": None,
                             "status": "no_same_area_transaction_in_official_export"},
                            {"monthOffset": 24, "countInMonth": 1, "record": copy.deepcopy(case["transactions"][-1])}]
        self.write_json("housing-story-evidence.json", self.data)

    def verify(self):
        with patch("requests.get", side_effect=AssertionError("network forbidden")), \
                patch("requests.post", side_effect=AssertionError("network forbidden")):
            return verifier.verify_story(self.repo, now=self.now,
                                         started_at=(self.now - dt.timedelta(minutes=1)).isoformat())

    def error(self, code, result=None):
        result = self.verify() if result is None else result
        self.assertEqual(result["status"], "failed", result)
        self.assertIn(code, [error["code"] for error in result["errors"]], result)

    def test_real_csv_shape_matching_links_and_independent_cashflow_pass(self):
        result = self.verify()
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["summary"]["mode"], "matched")
        for key in ("actualCsvEvidence", "currentLinkage", "scenarioAccounting", "sourceSeparation"):
            self.assertIs(result["checks"][key], True)
        numbers = result["checks"]["accounting"]["values"]
        self.assertEqual(Decimal(numbers["loan"]), Decimal(29700))
        self.assertEqual(Decimal(numbers["acquisitionReserve"]), Decimal(3960))
        self.assertAlmostEqual(Decimal(numbers["illustrativeGainBeforeTax"]),
                               Decimal(numbers["saleEquityAfterSaleReserveBeforeTax"])
                               - Decimal(numbers["totalCashContributed"]), places=8)
        self.assertFalse(result["summary"]["networkAccessed"])
        self.assertFalse(result["summary"]["paidAiInvoked"])

    def test_obituary_reference_is_valid_without_forced_current_link(self):
        self.write_page("대출 회사 대표 모친상")
        result = self.verify()
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["summary"]["mode"], "reference")
        self.assertEqual(result["checks"]["linkage"]["articleDetailCount"], 1)

    def test_raw_csv_byte_mutation_fails_hash(self):
        path = self.repo / "housing-story-evidence" / "fixture.csv"
        path.write_bytes(path.read_bytes().replace(b"142000", b"143000"))
        self.error("ACTUAL_CSV_EVIDENCE_INVALID")

    def test_record_price_mutation_cannot_reuse_existing_csv_hash(self):
        self.data["cases"][0]["transactions"][-1]["priceManwon"] += 1
        self.write_json("housing-story-evidence.json", self.data)
        self.error("ACTUAL_CSV_EVIDENCE_INVALID")

    def test_cancelled_record_is_rejected(self):
        self.data["cases"][0]["transactions"][-1]["cancelled"] = True
        self.write_json("housing-story-evidence.json", self.data)
        self.error("ACTUAL_CSV_EVIDENCE_INVALID")

    def test_builder_accounting_bug_is_caught_even_rebuilt_body_agrees(self):
        original = story.scenario_calculation
        def broken(case):
            result = original(case)
            result["illustrativeGainBeforeTax"] += Decimal(1)
            return result
        with patch.object(story, "scenario_calculation", side_effect=broken):
            self.write_page()
            self.error("SCENARIO_ACCOUNTING_MISMATCH")

    def test_amount_in_copy_cannot_be_replaced_by_inflated_gain(self):
        page = self.page()
        box = page.find(id="housingstory")
        case = story.load_story_evidence(self.repo / "housing-story-evidence.json")["cases"][0]
        original = verifier._amount(verifier.independent_accounting(case)["illustrativeGainBeforeTax"])
        box["data-copy-text"] = box["data-copy-text"].replace(original, "999,999만원")
        self.save(page)
        self.error("SCENARIO_AMOUNT_MISSING")

    def test_reconstruction_label_removal_fails(self):
        page = self.page()
        box = page.find(id="housingstory")
        box["data-copy-text"] = box["data-copy-text"].replace("[가상 인물·재구성]", "[실제 투자자]")
        self.save(page)
        self.error("FACT_RECONSTRUCTION_GUARD")

    def test_future_profit_guarantee_is_rejected(self):
        page = self.page()
        page.find(id="housingstory")["data-copy-text"] += "\n\n현재 매수하면 수익을 보장합니다."
        self.save(page)
        self.error("FUTURE_GUARANTEE")

    def test_source_label_and_source_url_cannot_leak_into_copy(self):
        original = (self.repo / "briefing.html").read_text(encoding="utf-8")
        for extra, code in (("\n자료: 국토부", "COPY_SOURCE_LEAK"),
                            ("\n" + story.PAGE, "COPY_SOURCE_URL_LEAK")):
            with self.subTest(code=code):
                page = BeautifulSoup(original, "html.parser")
                page.find(id="housingstory")["data-copy-text"] += extra
                self.save(page)
                self.error(code)

    def test_current_headline_change_requires_corresponding_story_rebuild(self):
        page = self.page()
        page.find(id="realestate")["data-copy-text"] = self.news.replace("주택 대출 규제 강화", "현재 주택 대출 규제 변경")
        self.save(page)
        self.error("CURRENT_LINKAGE_MISMATCH")

    def test_article_original_link_cannot_be_removed(self):
        page = self.page()
        box = page.find(id="housingstory")
        url = self.catalog["articles"][3]["url"]
        box["data-copy-text"] = box["data-copy-text"].replace(url, "")
        self.save(page)
        self.error("ARTICLE_DETAIL_LINK_MISSING")

    def test_visible_only_price_change_cannot_hide_behind_correct_copy(self):
        page = self.page()
        for node in page.find(id="housingstory").find_all(string=True):
            if "99,000만원" in str(node):
                node.replace_with(str(node).replace("99,000만원", "19,000만원", 1))
                break
        self.save(page)
        self.error("VISIBLE_COPY_CONTENT_MISMATCH")

    def test_footer_and_copy_button_are_required(self):
        original = (self.repo / "briefing.html").read_text(encoding="utf-8")
        page = BeautifulSoup(original, "html.parser")
        page.find(id="housingstory").find_parent("section").select_one(".source-note").decompose()
        self.save(page)
        self.error("SOURCE_FOOTER_MISSING")
        page = BeautifulSoup(original, "html.parser")
        page.find(id="housingstory").find_parent("section").select_one(".copy-btn")["onclick"] = "copySection('realestate')"
        self.save(page)
        self.error("COPY_HANDLER_MISMATCH")

    def test_dynamic_date_weekday_and_run_window_are_checked(self):
        page = self.page()
        box = page.find(id="housingstory")
        box["data-copy-text"] = box["data-copy-text"].replace(verifier.WEEKDAYS[self.now.weekday()], "잘못된요일", 1)
        self.save(page)
        self.error("DATE_WEEKDAY_MISMATCH")
        self.write_page()
        self.error("STORY_COLLECTION_TIME", verifier.verify_story(
            self.repo, now=self.now + dt.timedelta(hours=1), started_at=(self.now + dt.timedelta(minutes=1)).isoformat()))

    def test_negative_cost_adjusted_gain_is_not_presented_as_success(self):
        with patch.object(story, "SCENARIO_MONTHLY_HOLDING_BUDGET", Decimal(5000)):
            self.write_page()
            result = self.verify()
            self.assertEqual(result["status"], "passed", result)
            self.assertLess(Decimal(result["checks"]["accounting"]["values"]["illustrativeGainBeforeTax"]), 0)
            page = self.page()
            box = page.find(id="housingstory")
            box["data-copy-text"] = box["data-copy-text"].replace("예시 차익이 0 이하", "성공한 수익")
            self.save(page)
            self.error("NEGATIVE_GAIN_GUARD")

    def test_missing_card_fail_and_cli_report_exit_code(self):
        report = self.repo / "reports" / "story.json"
        self.assertEqual(verifier.main(["--repo-dir", str(self.repo), "--report", str(report)]), 0)
        self.assertEqual(verifier.read_json(report)["status"], "passed")
        page = self.page()
        page.find(id="housingstory").find_parent("section").decompose()
        self.save(page)
        self.assertEqual(verifier.main(["--repo-dir", str(self.repo), "--report", str(report)]), 1)
        self.error("CARD_MISSING_OR_DUPLICATE", verifier.read_json(report))

    def test_duplicate_json_input_never_echoes_payload(self):
        (self.repo / "housing-story-evidence.json").write_text(
            '{"test-sensitive-payload": 1, "test-sensitive-payload": 2}', encoding="utf-8")
        result = self.verify()
        self.error("JSON_DUPLICATE_KEY", result)
        self.assertNotIn("test-sensitive-payload", json.dumps(result))

    def test_declared_timeline_counts_and_zero_months_are_reproduced(self):
        self.add_timeline_fixture()
        result = self.verify()
        self.assertEqual(result["status"], "passed", result)
        months = result["checks"]["timelineEvidence"][0]["months"]
        self.assertEqual([item["months"] for item in months], [0, 6, 12, 24])
        self.assertEqual([item["officialContractCount"] for item in months], [2, 0, 0, 1])
        self.data["cases"][0]["timeline"][1]["countInMonth"] = 1
        self.write_json("housing-story-evidence.json", self.data)
        self.error("TIMELINE_MONTH_COUNT")

    def test_later_contract_cannot_be_called_first_contract(self):
        self.add_timeline_fixture()
        record = self.data["cases"][0]["timeline"][0]["record"]
        record.update(date="2017-08-30", priceManwon=100000, floor=22)
        self.write_json("housing-story-evidence.json", self.data)
        self.error("TIMELINE_NOT_FIRST_CONTRACT")

    def test_zero_month_cannot_be_interpolated_from_other_month(self):
        self.add_timeline_fixture()
        self.data["cases"][0]["timeline"][1]["record"] = copy.deepcopy(self.data["cases"][0]["transactions"][0])
        self.write_json("housing-story-evidence.json", self.data)
        self.error("TIMELINE_ZERO_NOT_PRESERVED")

    def test_unselected_monthly_export_also_requires_valid_hash(self):
        self.add_timeline_fixture()
        path = self.repo / "housing-story-evidence" / "year-2018.csv"
        path.write_bytes(path.read_bytes() + b"\n")
        self.error("TIMELINE_EXPORT_HASH")


if __name__ == "__main__":
    unittest.main()
