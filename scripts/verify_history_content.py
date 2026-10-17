"""Offline regression gate for the published historical-housing cards.

This command reads generated HTML and committed official data only.  It never
imports the briefing generator, fetches a feed/API, or invokes an AI service.
Browser clipboard checks are a separate gate; here we check the copy payload,
source separation, article links and independently calculated article-local data.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

from bs4 import BeautifulSoup

import history_prices as hp
import housing_history as hh
from content_rendering import render_history_links
from free_content import render_news_links


KST = dt.timezone(dt.timedelta(hours=9))
AI_KEYS = ("politics", "economy", "realestate", "world", "finance", "ai")
SOURCE_LABEL = re.compile(r"(?m)^\s*(?:자료|공식\s*출처|출처)\s*:")
WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")
DEFAULT_REPO = Path(__file__).resolve().parents[1]


class VerificationFailure(Exception):
    """Only fixed, credential-free messages are allowed in reports/logs."""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(code)


def require(condition, code, message):
    if not condition:
        raise VerificationFailure(code, message)


def _unique_json(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "JSON_DUPLICATE_KEY", "입력 JSON에 중복 키가 있습니다.")
        value[key] = item
    return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_json)


def timestamp(raw):
    value = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    require(value.tzinfo is not None, "TIMESTAMP_TIMEZONE", "검증 시각에 시간대가 없습니다.")
    return value.astimezone(dt.timezone.utc)


def shifted_month(month, count):
    """Calendar arithmetic deliberately independent of the production helper."""
    require(bool(re.fullmatch(r"\d{6}", month)), "PRICE_MONTH_INVALID", "비교 기준월 형식이 잘못됐습니다.")
    year, month_number = int(month[:4]), int(month[4:])
    require(1 <= month_number <= 12, "PRICE_MONTH_INVALID", "비교 기준월 형식이 잘못됐습니다.")
    year, zero_month = divmod(year * 12 + month_number - 1 + count, 12)
    return f"{year:04d}{zero_month + 1:02d}"


def month_label(month):
    return month[:4] + "-" + month[4:]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _card(page, name):
    boxes = page.find_all(id=name)
    require(len(boxes) == 1, "CARD_MISSING_OR_DUPLICATE", "필수 카드가 없거나 중복됐습니다.")
    box = boxes[0]
    require(box.has_attr("data-copy-text"), "COPY_PAYLOAD_MISSING", "카드의 복사 본문이 없습니다.")
    section = box.find_parent("section")
    require(section is not None, "CARD_SECTION_MISSING", "카드 영역이 분리되지 않았습니다.")
    button = section.select_one(".copy-btn")
    require(button is not None and button.get("onclick") == f"copySection('{name}')",
            "COPY_HANDLER_MISMATCH", "카드 복사 버튼의 연결이 잘못됐습니다.")
    return box, section


def _source_split(box, section, name):
    copied = box["data-copy-text"]
    require(not SOURCE_LABEL.search(copied) and not SOURCE_LABEL.search(box.get_text()),
            "COPY_SOURCE_LEAK", "자료·출처 문구가 복사 본문에 포함됐습니다.")
    footer = section.select_one(".source-note")
    require(footer is None or footer.find_parent(id=name) is None,
            "SOURCE_INSIDE_TEXTBOX", "자료·출처가 본문 상자 안에 있습니다.")
    footer_urls = [link.get("href") for link in footer.select("a[href]")] if footer else []
    require(all(url and url not in copied for url in footer_urls),
            "COPY_SOURCE_URL_LEAK", "출처 목록 주소가 복사 본문에 포함됐습니다.")
    if name == "historycompare":
        require(footer is not None and "자료:" in footer.get_text() and "출처:" in footer.get_text(),
                "HISTORY_SOURCE_FOOTER_MISSING", "과거 비교의 화면 하단 자료·출처가 없습니다.")
        require(all(url in footer_urls for url in (hp.TABLE_URL, hp.METHOD_URL)),
                "HISTORY_OFFICIAL_SOURCE_MISSING", "공식 가격 자료의 출처 연결이 없습니다.")
        require("과거 기사 표본은" not in copied and "무료 당시 보도입니다" not in copied,
                "COPY_PUBLISHER_LEAK", "언론사 출처 안내가 복사 본문에 포함됐습니다.")
    return {"copySha256": digest(copied), "sourceFooterPresent": footer is not None,
            "sourceListUrlsExcluded": True, "sourceListUrlCount": len(footer_urls)}


def _require_detail(box, url, title):
    links = [link for link in box.select("a[href]") if link.get("href") == url]
    require(any(link.get_text() == title for link in links),
            "ARTICLE_TITLE_LINK_MISSING", "기사 제목의 원문 링크가 없거나 다릅니다.")
    require(url in box["data-copy-text"], "COPY_ARTICLE_LINK_MISSING", "복사 본문에 기사 원문 주소가 없습니다.")


def _visible_content(box, renderer):
    expected = BeautifulSoup(renderer(box["data-copy-text"]), "html.parser").decode_contents()
    require(box.decode_contents() == expected, "VISIBLE_COPY_CONTENT_MISMATCH",
            "화면의 기사·가격 내용과 복사 본문이 다릅니다.")


def _verify_matching_and_prices(page, catalog, cache):
    news_box, news_section = _card(page, "realestate")
    history_box, history_section = _card(page, "historycompare")
    require(news_section is not history_section, "NEWS_HISTORY_NOT_SEPARATE", "기존 뉴스와 과거 비교가 같은 카드입니다.")
    copied, news_text = history_box["data-copy-text"], news_box["data-copy-text"]
    require("과거 기사 ·" not in news_text and "비교 주제:" not in news_text,
            "ORIGINAL_NEWS_CONTAMINATED", "과거 비교가 기존 뉴스 본문에 섞였습니다.")
    for block in news_text.split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 2 and hh.is_naver_article(lines[-1]):
            _require_detail(news_box, lines[-1], lines[0])
    news = hh.current_articles(news_text)
    groups = hh.select_comparisons(news, catalog["articles"])
    mode = "current_headline_match" if groups else "historical_reference_no_current_match"
    if not groups:
        require("오늘 기사와의 유사 사례가 아닌 과거 공급대책 참고 자료" in copied
                and "현재 기사:" not in copied,
                "NO_MATCH_REFERENCE_MODE", "노매치 상태의 참고 자료 안내가 잘못됐습니다.")
        require("유사점(두 과거 기사):" in copied and "차이점(두 시기):" in copied,
                "NO_MATCH_COMPARISON_NOTES", "과거 참고 사례의 유사점·차이점이 없습니다.")
        groups = [{"current": None, "topic": "supply", "label": "공급대책 참고 사례", "hits": [],
                   "references": [article for article in catalog["articles"] if "supply" in article["topics"]]}]
    else:
        require("오늘 기사와의 유사 사례가 아닌" not in copied,
                "MATCH_MODE_MISMATCH", "매칭 결과와 참고 모드 안내가 다릅니다.")
    require("통계 범위: 한국부동산원 월간 아파트 매매가격지수 · 서울" in copied,
            "PRICE_SCOPE_MISSING", "서울 월간 아파트 지수의 범위 안내가 없습니다.")
    require("공식 지수 기준: " + cache["basis"] in copied,
            "PRICE_BASIS_MISMATCH", "공식 지수 기준시점이 원자료와 다릅니다.")
    for phrase in ("기사월 지수에는 발행일 뒤의 같은 달 조사도 포함될 수 있습니다",
                   "같은 표·같은 지역의 지수만 사용", "반올림 전 값으로 계산",
                   "서울 지수로 다른 지역의 변화를 설명하지 않습니다",
                   "정책의 효과를 분리한 결과도, 현재의 상승 예측도 아닙니다"):
        require(phrase in copied, "PRICE_METHOD_NOTICE_MISSING", "가격 비교의 해석·계산 범위 안내가 없습니다.")
    require(not any(phrase in copied for phrase in ("공식 월간 지수를 확인하지 못했습니다",
                                                  "공식 해당 월 자료 미확인", "확인 실패")),
            "HISTORY_DATA_UNAVAILABLE", "과거 비교에 자료 확인 실패 표시가 있습니다.")
    blocks = copied.split("\n\n")
    starts = [index for index, block in enumerate(blocks) if block.startswith("비교 주제: ")]
    require(len(starts) == len(groups), "MATCH_GROUP_COUNT", "현재 기사별 비교 묶음 수가 다릅니다.")
    calculations, reports = [], []
    for group_number, group in enumerate(groups):
        start = starts[group_number]
        end = (starts[group_number + 1] if group_number + 1 < len(starts)
               else next((i for i in range(start + 1, len(blocks)) if blocks[i].startswith("읽는 법:")), len(blocks)))
        group_blocks = blocks[start:end]
        require(group_blocks[0] == "비교 주제: " + group["label"],
                "MATCH_TOPIC_ORDER", "기사별 주제 또는 순서가 매칭 결과와 다릅니다.")
        current = group["current"]
        if current:
            require("현재 기사: " + current["title"] + "\n" + current["url"] in group_blocks,
                    "CURRENT_ARTICLE_MISMATCH", "현재 기사 연결이 매칭 결과와 다릅니다.")
            _require_detail(history_box, current["url"], "현재 기사: " + current["title"])
            similarity = ("유사점: 현재 제목의 ‘" + "·".join(group["hits"]) + "’와 과거 보도의 ‘"
                          + group["label"] + "’ 주제가 겹칩니다. 조치의 강도·효과까지 같다는 뜻은 아닙니다.")
            require(similarity in group_blocks, "SIMILARITY_MISMATCH", "현재·과거 기사의 유사점 설명이 다릅니다.")
            require(not hh.EXCLUDE.search(current["title"]), "IRRELEVANT_ARTICLE_MATCH", "무관한 기사를 과거 기사에 연결했습니다.")
        positions = [i for i, block in enumerate(group_blocks) if block.startswith("과거 기사 · ")]
        require(len(positions) == len(group["references"]),
                "HISTORICAL_ARTICLE_COUNT", "기사 묶음의 과거 기사 수가 다릅니다.")
        for index, article in enumerate(group["references"]):
            position = positions[index]
            expected_article = f"과거 기사 · {article['era']} · {article['date']}\n{article['title']}\n{article['url']}"
            require(group_blocks[position] == expected_article,
                    "HISTORICAL_ARTICLE_IDENTITY", "과거 기사의 정부·일자·제목·주소가 다릅니다.")
            _require_detail(history_box, article["url"], article["title"])
            require(position + 2 < len(group_blocks), "ARTICLE_DATA_BLOCK_MISSING", "과거 기사 옆의 가격·차이점 자료가 없습니다.")
            require(group_blocks[position + 2] == "당시 내용: " + article["fact"] + "\n차이점: " + article["difference"],
                    "ARTICLE_DIFFERENCE_MISMATCH", "과거 기사별 당시 내용·차이점이 다릅니다.")
            base_month = dt.date.fromisoformat(article["date"]).strftime("%Y%m")
            monthly = cache["series"]["서울"]["monthly"]
            base = monthly[base_month]
            lines = [f"서울 아파트 매매가격지수 · 기사월 {month_label(base_month)} = {base:.4f}"]
            horizons = []
            for months, label in ((6, "6개월"), (12, "1년"), (24, "2년")):
                target = shifted_month(base_month, months)
                target_index = monthly[target]
                change = (target_index / base - 1) * 100
                lines.append(f"이후 {label} ({month_label(target)}): {change:+.2f}% · 지수 {target_index:.4f}")
                horizons.append({"months": months, "comparisonMonth": target, "index": target_index,
                                 "changePctUnrounded": change, "displayedChange": f"{change:+.2f}%"})
            require(group_blocks[position + 1].splitlines() == lines,
                    "ARTICLE_PRICE_CALCULATION", "기사별 비교월·원지수·6개월·1년·2년 변화율이 독립 계산과 다릅니다.")
            calculations.append({"group": group_number, "articleId": article["id"], "era": article["era"],
                                 "articleDate": article["date"], "baseMonth": base_month, "baseIndex": base,
                                 "horizons": horizons})
        missing = [era for era in hh.ERAS if not any(article["era"] == era for article in group["references"])]
        if missing:
            expected = ("표본 범위: 이 주제와 연결할 " + "·".join(missing)
                        + "의 검증된 기사가 아직 없어 다른 주제 기사로 대체하지 않았습니다.")
            require(expected in group_blocks, "MISSING_ERA_NOTICE", "표본이 없는 정부의 범위 안내가 없습니다.")
        reports.append({"topic": group["topic"], "currentArticleUrl": current["url"] if current else None,
                        "references": [article["id"] for article in group["references"]]})
    require(len(re.findall(r"(?m)^과거 기사 · ", copied)) == len(calculations),
            "UNEXPECTED_HISTORICAL_ARTICLE", "검증된 묶음 밖에 과거 기사가 있습니다.")
    require(len(re.findall(r"(?m)^현재 기사: ", copied)) == sum(group["current"] is not None for group in groups),
            "UNEXPECTED_CURRENT_ARTICLE", "검증된 묶음 밖에 현재 기사 연결이 있습니다.")
    source_split = {"historycompare": _source_split(history_box, history_section, "historycompare"),
                    "realestate": _source_split(news_box, news_section, "realestate")}
    _visible_content(history_box, render_history_links)
    _visible_content(news_box, render_news_links)
    return {"mode": mode, "currentArticleCount": len(news), "groups": reports,
            "articleLocalCalculations": calculations, "existingNewsSeparate": True,
            "sourceSplit": source_split}


def _verify_timing(page, cache, catalog, now, started_at):
    history = page.find(id="historycompare")["data-copy-text"]
    news = page.find(id="realestate")["data-copy-text"]
    matched = re.search(r"(?m)^매칭 수집: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \(한국시간\)$", history)
    require(matched is not None, "COLLECTION_TIME_MISSING", "매칭 수집 시각이 없습니다.")
    collected = dt.datetime.strptime(matched[1], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    date_line = f"{collected.year % 100}년 {collected.month}월 {collected.day}일 {WEEKDAYS[collected.weekday()]}"
    require(history.splitlines()[1] == date_line and news.splitlines()[1] == date_line,
            "DATE_WEEKDAY_MISMATCH", "뉴스·과거 비교의 날짜와 요일이 수집일과 다릅니다.")
    end = now.astimezone(KST).replace(second=0, microsecond=0)
    require(collected <= end, "COLLECTION_TIME_FUTURE", "매칭 수집 시각이 검증 시각보다 늦습니다.")
    if started_at:
        start = timestamp(started_at).astimezone(KST).replace(second=0, microsecond=0)
        require(start <= collected <= end, "COLLECTION_TIME_OUTSIDE_RUN", "매칭 수집 시각이 이번 실행 범위 밖입니다.")
    downloaded = timestamp(cache["downloadedAt"])
    age = collected - downloaded
    # Collection labels have minute precision, while the cache has seconds.
    require(age >= -dt.timedelta(minutes=1), "CACHE_TIME_FUTURE", "공식 통계 수집일이 매칭 시각보다 늦습니다.")
    require(age < hp.MAX_AGE, "OFFICIAL_CACHE_EXPIRED", "공식 통계 보관본을 30일 안에 갱신하지 못했습니다.")
    require("30일 이내 보관본" in history or "공식 API 신규 수집" in history,
            "CACHE_STATUS_MISSING", "공식 통계의 최신 수집·보관 상태가 없습니다.")
    require("이전 공식 자료 보관본 사용" not in history,
            "OFFICIAL_CACHE_FALLBACK", "공식 통계 재확인이 실패하여 이전 보관본을 사용했습니다.")
    require("통계 수집: " + downloaded.astimezone(KST).strftime("%Y-%m-%d %H:%M") + " (한국시간)" in history,
            "CACHE_TIMESTAMP_MISMATCH", "공식 통계 수집일 표시가 보관본과 다릅니다.")
    require("과거 기사 표본 검토: " + catalog["reviewed"] in history,
            "CATALOG_REVIEW_DATE_MISMATCH", "과거 기사 표본 검토일이 목록과 다릅니다.")
    return {"matchingCollectedAtKST": collected.isoformat(), "priceDownloadedAt": cache["downloadedAt"],
            "historicalCacheAgeSeconds": max(0, age.total_seconds()), "cachePolicyDays": hp.MAX_AGE.days,
            "dateAndWeekday": date_line, "checkedWithinRun": bool(started_at)}


def _ai_body(raw):
    return raw.replace("\r\n", "\n").split("\n\n", 1)[-1].strip()


def _verify_ai(page, cache, before, event):
    require(event in ("schedule", "workflow_dispatch"), "EVENT_INVALID", "지원하지 않는 실행 종류입니다.")
    if event == "schedule":
        require(os.environ.get("REFRESH_COMMENTS", "false").strip().lower() != "true",
                "SCHEDULE_AI_REFRESH_ENABLED", "예약 실행에서 유료 AI 갱신이 켜졌습니다.")
    require(isinstance(cache, dict) and all(isinstance(cache.get(key), dict)
            and isinstance(cache[key].get("text"), str) and cache[key]["text"].strip()
            and isinstance(cache[key].get("updated"), str) and cache[key]["updated"].strip() for key in AI_KEYS),
            "AI_CACHE_INCOMPLETE", "기존 AI 논평 캐시 6개 중 비어 있는 항목이 있습니다.")
    if before is not None:
        before_cache = before.get("aiCache", before)
        require(isinstance(before_cache, dict), "AI_BASELINE_INVALID", "AI 논평 기준선 형식이 잘못됐습니다.")
        require(all(isinstance(before_cache.get(key), dict)
                    and before_cache[key].get("text") == cache[key]["text"]
                    and before_cache[key].get("updated") == cache[key]["updated"] for key in AI_KEYS),
                "AI_CACHE_CHANGED", "자동 갱신에서 기존 AI 논평 내용 또는 생성일이 바뀌었습니다.")
    hashes = {}
    for key in AI_KEYS:
        box, _ = _card(page, key + "_comment")
        body = box["data-copy-text"]
        require(_ai_body(body) == cache[key]["text"].strip(),
                "AI_HTML_BODY_CHANGED", "HTML의 AI 논평 본문이 기존 캐시와 다릅니다.")
        require(box.get_text() == body, "AI_VISIBLE_BODY_CHANGED", "AI 논평의 화면 본문과 복사 본문이 다릅니다.")
        require(cache[key]["updated"] in body.split("\n\n", 1)[0],
                "AI_UPDATED_LABEL_MISMATCH", "AI 논평의 기존 생성일 표시가 다릅니다.")
        if before is not None and "aiBodies" in before:
            require(key in before["aiBodies"] and _ai_body(before["aiBodies"][key]) == _ai_body(body),
                    "AI_BASELINE_BODY_CHANGED", "AI 논평의 본문이 실행 전 HTML과 다릅니다.")
        hashes[key] = {"textSha256": digest(cache[key]["text"]), "updatedSha256": digest(cache[key]["updated"])}
    return {"cacheCount": len(AI_KEYS), "baselineCompared": before is not None, "hashes": hashes,
            "dateTitleExcludedFromBodyComparison": True, "paidAIInvokedByValidator": False}


def verify_content(repo_dir=DEFAULT_REPO, html_path=None, ai_before=None, event="schedule", started_at=None, now=None):
    """Return a safe JSON-serializable report; failure details never echo input."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    result = {"schemaVersion": 1, "status": "failed", "checkedAtUtc": now.astimezone(dt.timezone.utc).isoformat(),
              "checks": {"articleMatching": False, "prices": False, "sourceSeparation": False,
                         "aiCacheNonempty": False, "aiCacheUnchanged": None, "aiSnapshotCompared": False},
              "errors": [], "summary": {"networkAccessed": False, "paidAIInvoked": False}}
    repo_dir = Path(repo_dir)
    html_path = Path(html_path) if html_path else repo_dir / "briefing.html"
    try:
        cache = read_json(repo_dir / "history-price-cache.json")
        catalog = read_json(repo_dir / "housing-history-articles.json")
        require(hp._valid_cache(cache) and bool(str(cache.get("basis", "")).strip()),
                "OFFICIAL_CACHE_INVALID", "공식 통계의 표·지역·주기·원지수 범위가 올바르지 않습니다.")
        require(hp.MAX_AGE == dt.timedelta(days=30), "CACHE_POLICY_CHANGED", "공식 통계의 30일 보관 기준이 바뀌었습니다.")
        require(isinstance(catalog.get("articles"), list) and catalog["articles"],
                "CATALOG_INVALID", "과거 기사 목록이 비어 있거나 잘못됐습니다.")
        raw_html = html_path.read_text(encoding="utf-8-sig")
        page = BeautifulSoup(raw_html, "html.parser")
        result["checks"]["officialCache"] = {"table": cache["table"], "cycle": cache["cycle"],
                                               "item": cache["item"], "basis": cache["basis"],
                                               "regions": {region: data["observed"] for region, data in cache["series"].items()}}
        result["checks"]["content"] = _verify_matching_and_prices(page, catalog, cache)
        result["checks"].update(articleMatching=True, prices=True, sourceSeparation=True)
        result["checks"]["timing"] = _verify_timing(page, cache, catalog, now, started_at)
        before = read_json(ai_before) if isinstance(ai_before, (str, Path)) else ai_before
        ai_cache = read_json(repo_dir / "comment-cache.json")
        result["checks"]["ai"] = _verify_ai(page, ai_cache, before, event)
        result["checks"].update(aiCacheNonempty=True, aiCacheUnchanged=True if before is not None else None,
                                 aiSnapshotCompared=before is not None)
        result["status"] = "passed"
        result["summary"].update({"mode": result["checks"]["content"]["mode"],
                                  "articlesVerified": len(result["checks"]["content"]["articleLocalCalculations"]),
                                  "htmlSha256": digest(raw_html), "actualBrowserClipboard": "separate_gate_required"})
    except VerificationFailure as error:
        result["errors"].append({"code": error.code, "message": error.message})
    except (OSError, UnicodeError):
        result["errors"].append({"code": "INPUT_READ_FAILED", "message": "검증 입력 파일을 읽지 못했습니다."})
    except (KeyError, ValueError, TypeError, IndexError):
        result["errors"].append({"code": "INPUT_SCHEMA_INVALID", "message": "검증 입력의 구조 또는 날짜가 올바르지 않습니다."})
    except Exception:
        # Never serialize arbitrary exception text: future dependencies may
        # include environment values or other sensitive material in messages.
        result["errors"].append({"code": "VALIDATION_INTERNAL_ERROR", "message": "콘텐츠 자동 점검을 완료하지 못했습니다."})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ai-before", type=Path)
    parser.add_argument("--event", choices=("schedule", "workflow_dispatch"), default="schedule")
    parser.add_argument("--started-at")
    args = parser.parse_args(argv)
    report = verify_content(args.repo_dir, args.html, args.ai_before, args.event, args.started_at)
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        print(json.dumps({"status": "failed", "errorCode": "REPORT_WRITE_FAILED"}))
        return 1
    print(json.dumps({"status": report["status"], "errorCodes": [error["code"] for error in report["errors"]],
                      "articlesVerified": report["summary"].get("articlesVerified", 0)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
