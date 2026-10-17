#!/usr/bin/env python3
"""Exercise the real copy buttons in isolated Chromium pages, without collectors.

Only the supplied generated HTML and two clearly labelled offline fixtures are
served.  The fixtures use the production history builder, section renderer and
PAGE_TEMPLATE (including copySection), with the checked-in official price cache.
No article is opened, no source is fetched and no AI operation is invoked.
"""

import argparse
import contextlib
import datetime as dt
import functools
import hashlib
import http.server
import json
import re
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch


SOURCE_LABEL = re.compile(r"^\s*(?:자료|공식\s*출처|출처)\s*:", re.MULTILINE)
URL = re.compile(r"https?://[^\s<>]+")
KST = dt.timezone(dt.timedelta(hours=9))
INSTALL_GUIDANCE = ("Playwright/Chromium is unavailable. Install requirements-checks.txt "
                    "and run: python -m playwright install --with-deps chromium")


class CheckFailure(Exception):
    """A fixed, non-secret diagnostic suitable for CI reports."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _deny_get(*_args, **_kwargs):
    raise CheckFailure("unexpected_network", "An offline fixture attempted a source request.")


def _copy_policy_errors(text, detail_urls, source_urls, table_urls, source_text=""):
    errors = []
    if SOURCE_LABEL.search(text):
        errors.append("source_label_copied")
    if any(url in text for url in table_urls):
        errors.append("statistics_source_url_copied")
    # A source list may repeat a genuine article detail URL; the latter remains
    # part of the useful copy content by design.
    if any(url in text for url in set(source_urls) - set(detail_urls)):
        errors.append("source_list_url_copied")
    paragraphs = [SOURCE_LABEL.sub("", line).strip() for line in source_text.splitlines()
                  if SOURCE_LABEL.match(line)]
    if any(len(line) > 20 and line in text for line in paragraphs):
        errors.append("source_description_copied")
    if any(url not in text for url in detail_urls):
        errors.append("article_detail_url_missing")
    return errors


def _build_fixture_pages(repo_dir, serving_dir):
    """Render matching and unrelated inputs using production code, offline."""
    scripts_dir = str(repo_dir / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # The generator is import-safe.  Never call build_page/main/commentary.
    # Blocking requests during import and rendering makes that boundary fail
    # closed if a future import accidentally starts a collector.
    with patch("requests.sessions.Session.request", side_effect=_deny_get):
        import generate_briefing as rendering
        import housing_history as history
        import history_prices as prices

        dataset = json.loads((repo_dir / "history-price-cache.json").read_text(encoding="utf-8"))
        if not prices._valid_cache(dataset):
            raise CheckFailure("official_cache_invalid", "The official historical price cache is invalid.")
        dataset = {**dataset, "status": "cache", "stale": False,
                   "note": "오프라인 자동 점검용 공식 보관본 · 공개 게시하지 않는 테스트"}
        catalog_path = repo_dir / "housing-history-articles.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))["articles"]
        now = dt.datetime.now(KST)
        fixtures = {
            "matched": ("[테스트 입력] 신규 택지 공급·개발 계획\n"
                        "https://n.news.naver.com/mnews/article/001/0000000001\n\n"
                        "[테스트 입력] DSR 대출 규제 검토\n"
                        "https://n.news.naver.com/mnews/article/001/0000000002"),
            "nomatch": ("[테스트 입력] 건설사 임원 모친상\n"
                        "https://n.news.naver.com/mnews/article/001/0000000003"),
        }
        results = []
        for mode, news in fixtures.items():
            selected = history.select_comparisons(history.current_articles(news), catalog)
            if (mode == "matched") != bool(selected):
                raise CheckFailure("fixture_selection_changed", "Offline matching/no-match fixture selection changed.")
            body = history.build_history_comparison(news, _deny_get, now,
                                                     dataset=dataset, catalog_path=catalog_path)
            with patch.object(rendering, "NOW", now):
                section = rendering.render_section("historycompare", "🕰️", "과거 비교 · 자동 점검용 테스트", body)
            page = rendering.PAGE_TEMPLATE.format(
                today="자동 점검용 테스트 · 공개 콘텐츠가 아닙니다", updated=now.isoformat(),
                actions_url="#disabled-in-offline-test", sections=section)
            filename = "fixture-" + mode + ".html"
            (serving_dir / filename).write_text(page, encoding="utf-8")
            references = ([a for group in selected for a in group["references"]]
                          if selected else [a for a in catalog if "supply" in a["topics"]])
            expected_urls = [a["url"] for a in references]
            if selected:
                expected_urls.extend(group["current"]["url"] for group in selected)
            results.append({"name": "fixture-" + mode, "file": filename,
                            "mode": mode, "expectedDetailUrls": expected_urls})
    return results, [prices.TABLE_URL, prices.METHOD_URL]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def list_directory(self, _path):
        self.send_error(404)
        return None


@contextlib.contextmanager
def _serve_only(serving_dir):
    handler = functools.partial(_QuietHandler, directory=str(serving_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, name="history-check-loopback", daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _require(condition, code, message):
    if not condition:
        raise CheckFailure(code, message)


def _check_card(page, card_id, table_urls, expected_detail_urls=None):
    textbox = page.locator("#" + card_id)
    _require(textbox.count() == 1, "card_missing_or_duplicate", "A required card is absent or duplicated.")
    _require(textbox.is_visible(), "card_hidden", "A required card is hidden.")
    expected = textbox.get_attribute("data-copy-text")
    _require(bool(expected and expected.strip()), "copy_text_empty", "A required card has empty copy text.")
    card = textbox.locator("xpath=ancestor::section[1]")
    button = card.locator("button.copy-btn")
    _require(button.count() == 1, "copy_button_missing", "A required card has no unique copy button.")
    _require(button.get_attribute("onclick") == "copySection('" + card_id + "')",
             "copy_handler_changed", "The card does not use its production copySection handler.")
    detail_urls = textbox.locator("a[href]").evaluate_all("links => links.map(a => a.href)")
    expected_urls = expected_detail_urls or detail_urls
    _require(bool(detail_urls), "article_links_missing", "The card has no rendered article detail links.")
    _require(all(url in detail_urls for url in expected_urls), "article_link_rendering_missing",
             "An expected article detail URL is absent from rendered title links.")
    source_note = card.locator(".source-note")
    source_urls = []
    source_text = ""
    if source_note.count():
        _require(source_note.count() == 1 and source_note.is_visible(), "source_footer_hidden",
                 "A rendered source footer is missing or hidden.")
        source_text = source_note.inner_text()
        source_urls = source_note.locator("a[href]").evaluate_all("links => links.map(a => a.href)")
    if card_id == "historycompare":
        _require(bool(source_text.strip()) and SOURCE_LABEL.search(source_text), "source_footer_missing",
                 "Historical comparison must retain its visible source footer.")
        _require(all(url in source_urls for url in table_urls), "statistics_footer_missing",
                 "Historical comparison lost a statistics table/method source link.")
        _require(all(label in expected for label in ("이후 6개월", "이후 1년", "이후 2년")),
                 "price_horizon_missing", "Historical comparison lost a required price horizon.")
    if card_id == "housingstory":
        _require(bool(source_text.strip()) and SOURCE_LABEL.search(source_text),
                 "story_source_footer_missing", "The story must retain visible attribution.")
        _require(all(label in expected for label in ("[실거래 확인]", "[가상 인물·재구성]", "[비용 가정]", "[한계]")),
                 "story_fact_fiction_split_missing", "The story lost its fact/fiction/cost distinction.")
    errors = _copy_policy_errors(expected, expected_urls, source_urls, table_urls, source_text)
    _require(not errors, errors[0] if errors else "copy_policy_failed",
             "The copy text violates the attribution/detail-link policy.")

    marker = "history-copy-check-" + uuid.uuid4().hex
    page.bring_to_front()
    page.evaluate("text => navigator.clipboard.writeText(text)", marker)
    button.click()
    page.wait_for_function("async marker => (await navigator.clipboard.readText()) !== marker",
                           arg=marker, timeout=10000)
    actual = page.evaluate("() => navigator.clipboard.readText()")
    _require(actual == expected, "clipboard_mismatch", "The real clipboard differs from data-copy-text.")
    _require(not _copy_policy_errors(actual, expected_urls, source_urls, table_urls, source_text),
             "clipboard_source_leak", "The real clipboard contains attribution or lost an article URL.")
    # Re-read after the click to catch code accidentally removing source notes.
    if source_text:
        _require(source_note.is_visible() and source_note.inner_text() == source_text,
                 "source_footer_changed", "Copying changed the visible source footer.")
    return {"card": card_id, "status": "passed", "clipboardEqualsDataCopyText": True,
            "attributionExcluded": True, "articleDetailUrlsRetained": len(expected_urls),
            "visibleSourceFooter": bool(source_text),
            "copyTextSha256": hashlib.sha256(actual.encode("utf-8")).hexdigest()}


def _assert_fixture_mode(text, mode):
    if mode == "matched":
        _require(all(label in text for label in ("현재 기사:", "유사점:", "차이점:")),
                 "matched_explanation_missing", "The matching fixture lost current article or comparison explanations.")
    elif mode == "nomatch":
        _require("현재 기사:" not in text and all(label in text for label in (
            "오늘 기사와의 유사 사례가 아닌", "유사점(두 과거 기사):", "차이점(두 시기):")),
            "nomatch_forced_analogy", "An unrelated article was forced into a historical analogy.")


def run_checks(args, report):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise CheckFailure("playwright_not_installed", INSTALL_GUIDANCE) from None
    repo_dir, html_path = args.repo_dir.resolve(), args.html.resolve()
    _require(html_path.is_file(), "html_missing", "The supplied generated HTML file does not exist.")
    screenshot_dir = args.screenshot_dir.resolve()
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="history-browser-check-") as tmp:
        serving_dir = Path(tmp)
        shutil.copyfile(html_path, serving_dir / "generated.html")
        fixtures, table_urls = _build_fixture_pages(repo_dir, serving_dir)
        cases = [{"name": "generated", "file": "generated.html",
                  "cards": ("realestate", "historycompare", "housingstory")},
                 *[{**fixture, "cards": ("historycompare",)} for fixture in fixtures]]
        with _serve_only(serving_dir) as origin, sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Exception:
                raise CheckFailure("chromium_unavailable", INSTALL_GUIDANCE) from None
            report["summary"].update(actualBrowser=True, engine="chromium")
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 1000},
                                               locale="ko-KR", service_workers="block")
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
                # An accidental article navigation, tracking pixel or future
                # source-fetching script cannot leave the loopback test origin.
                def route_request(route):
                    url = urlsplit(route.request.url)
                    if (url.scheme + "://" + url.netloc == origin
                            and route.request.method in ("GET", "HEAD")):
                        route.continue_()
                    else:
                        route.abort()
                context.route("**/*", route_request)
                page = context.new_page()
                for case in cases:
                    for card_id in case["cards"]:
                        label = case["name"] + "-" + card_id
                        try:
                            page.goto(origin + "/" + case["file"], wait_until="load", timeout=15000)
                            if case.get("mode"):
                                _assert_fixture_mode(page.locator("#" + card_id).get_attribute("data-copy-text") or "",
                                                     case["mode"])
                            item = _check_card(page, card_id, table_urls, case.get("expectedDetailUrls"))
                            item["page"] = case["name"]
                            if case.get("mode"):
                                item["fixtureMode"] = case["mode"]
                            if card_id == "housingstory":
                                report["storyCheck"] = item
                            else:
                                report["checks"].append(item)
                        except Exception as exc:
                            code = exc.code if isinstance(exc, CheckFailure) else "browser_operation_failed"
                            message = str(exc) if isinstance(exc, CheckFailure) else "Chromium copy verification failed."
                            error = {"check": label, "code": code, "message": message}
                            try:
                                screenshot_path = screenshot_dir / (label + ".png")
                                page.screenshot(path=str(screenshot_path), full_page=False)
                                error["screenshot"] = str(screenshot_path)
                            except Exception:
                                error["screenshotUnavailable"] = True
                            report["errors"].append(error)
                            failed = {"page": case["name"], "card": card_id, "status": "failed", "code": code}
                            if card_id == "housingstory":
                                report["storyCheck"] = failed
                            else:
                                report["checks"].append(failed)
                context.close()
            finally:
                browser.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    repo_default = Path(__file__).resolve().parents[1]
    parser.add_argument("--repo-dir", type=Path, default=repo_default)
    parser.add_argument("--html", type=Path, default=Path("briefing.html"))
    parser.add_argument("--report", type=Path, default=Path("history-browser-report.json"))
    parser.add_argument("--screenshot-dir", type=Path, default=Path("history-browser-screenshots"))
    args = parser.parse_args(argv)
    report = {"schemaVersion": 1, "status": "failed",
              "checkedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "checks": [], "errors": [], "summary": {"actualBrowser": False, "engine": None}}
    try:
        run_checks(args, report)
    except Exception as exc:
        report["errors"].append({
            "check": "browser_setup", "code": exc.code if isinstance(exc, CheckFailure) else "setup_failed",
            "message": str(exc) if isinstance(exc, CheckFailure) else "Offline browser check setup failed."})
    report["status"] = "failed" if report["errors"] else "passed"
    report["summary"].update({"passed": sum(x["status"] == "passed" for x in report["checks"])
                                      + int(report.get("storyCheck", {}).get("status") == "passed"),
                         "failed": len(report["errors"]),
                         "scope": "generated realestate/historycompare/housingstory and offline matched/no-match fixtures",
                         "externalNetworkPolicy": "loopback-only", "aiInvoked": False})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("History browser checks: " + report["status"] + "; "
          + str(report["summary"]["passed"]) + " passed; " + str(len(report["errors"])) + " errors")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
