"""Offline publication gate for an official-record housing reconstruction.

Reads retained CSVs and generated HTML only.  The current headline linkage and
rendered/copy contents are rebuilt without importing the briefing generator;
cash flows are independently recomputed with Decimal, rather than trusting the
story builder's arithmetic.  Actual clipboard behavior is a separate gate.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from bs4 import BeautifulSoup

import housing_story as story
from content_rendering import render_history_links, render_sources, split_content_sources
from official_transactions import _parse_csv


DEFAULT_REPO = Path(__file__).resolve().parents[1]
KST = dt.timezone(dt.timedelta(hours=9))
WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")
SOURCE_LABEL = re.compile(r"(?m)^\s*(?:자료|공식\s*출처|출처)\s*:")


class VerificationFailure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(code)


def require(condition, code, message):
    if not condition:
        raise VerificationFailure(code, message)


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "JSON_DUPLICATE_KEY", "검증 JSON에 중복 키가 있습니다.")
        value[key] = item
    return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_pairs)


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _no_network(*args, **kwargs):
    raise VerificationFailure("UNEXPECTED_NETWORK", "스토리 오프라인 검사에서 외부 접근을 요청했습니다.")


def _card(page, name):
    cards = page.find_all(id=name)
    require(len(cards) == 1, "CARD_MISSING_OR_DUPLICATE", "필수 카드가 없거나 중복됐습니다.")
    box = cards[0]
    require(isinstance(box.get("data-copy-text"), str), "COPY_PAYLOAD_MISSING", "복사 본문이 없습니다.")
    section = box.find_parent("section")
    require(section is not None, "CARD_SECTION_MISSING", "카드 영역이 분리되지 않았습니다.")
    buttons = section.select(".copy-btn")
    require(len(buttons) == 1 and buttons[0].get("onclick") == f"copySection('{name}')",
            "COPY_HANDLER_MISMATCH", "스토리 복사 버튼의 연결이 잘못됐습니다.")
    return box, section


def _generation_time(copied, now, started_at):
    stamps = re.findall(r"(?m)^갱신 확인: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \(한국시간\)$", copied)
    require(len(stamps) == 1, "STORY_COLLECTION_TIME", "스토리 갱신 확인 시각이 없거나 중복됐습니다.")
    generated = dt.datetime.strptime(stamps[0], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    require(generated <= now + dt.timedelta(minutes=1), "STORY_COLLECTION_TIME", "스토리 갱신 시각이 미래입니다.")
    if started_at is not None:
        stamp = dt.datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        require(stamp.tzinfo is not None, "RUN_TIMEZONE_MISSING", "실행 시각의 시간대가 없습니다.")
        require(generated >= stamp.astimezone(KST).replace(second=0, microsecond=0),
                "STORY_COLLECTION_TIME", "스토리 갱신 시각이 이번 실행보다 이릅니다.")
    lines = copied.splitlines()
    expected_date = (f"{generated.year % 100}년 {generated.month}월 {generated.day}일 "
                     + WEEKDAYS[generated.weekday()])
    require(len(lines) > 1 and lines[1] == expected_date, "DATE_WEEKDAY_MISMATCH", "스토리 날짜와 요일이 갱신 시각과 다릅니다.")
    return generated


def independent_accounting(case):
    """Recompute all cash flows without scenario_calculation or _amount."""
    first, last = case["transactions"][0], case["transactions"][-1]
    purchase, sale = Decimal(first["priceManwon"]), Decimal(last["priceManwon"])
    days = (last["date"] - first["date"]).days
    rounded = lambda value: value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    loan = rounded(purchase * story.SCENARIO_LOAN_RATIO)
    acquisition = rounded(purchase * story.SCENARIO_ACQUISITION_RESERVE_RATIO)
    sale_reserve = rounded(sale * story.SCENARIO_SALE_RESERVE_RATIO)
    years = Decimal(days) / Decimal(365)
    holding = story.SCENARIO_MONTHLY_HOLDING_BUDGET * years * Decimal(12)
    interest = loan * story.SCENARIO_ANNUAL_RATE * Decimal(days) / Decimal(365)
    monthly_interest = loan * story.SCENARIO_ANNUAL_RATE / Decimal(12)
    reserve = acquisition + sale_reserve + holding
    initial = purchase - loan + acquisition
    values = {
        "purchase": purchase, "sale": sale, "days": days, "loan": loan,
        "acquisitionReserve": acquisition, "saleReserve": sale_reserve,
        "holdingBudget": holding, "monthlyHoldingBudget": story.SCENARIO_MONTHLY_HOLDING_BUDGET,
        "costReserve": reserve, "initialCapital": initial,
        "interest": interest, "monthlyInterest": monthly_interest,
        "totalCashContributed": initial + interest + holding,
        "saleEquityBeforeTax": sale - loan,
        "saleEquityAfterSaleReserveBeforeTax": sale - loan - sale_reserve,
        "illustrativeGainBeforeTax": sale - purchase - reserve - interest,
        "priceChangePct": (sale / purchase - Decimal(1)) * Decimal(100),
        "stressAdditionalMonthlyInterest": loan * Decimal("0.02") / Decimal(12),
        "stressSaleEquity": sale * Decimal("0.8") - loan,
    }
    # Accounting identity uses cash actually contributed and sale costs,
    # independently of the builder's purchase/sale difference expression.
    require(abs(values["illustrativeGainBeforeTax"] -
                (values["saleEquityBeforeTax"] - values["totalCashContributed"] - sale_reserve)) < Decimal("0.00000001"),
            "ACCOUNTING_IDENTITY", "현금 투입과 매도 후 금액의 회계 항등식이 맞지 않습니다.")
    return values


def _amount(value):
    return f'{Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP):,.0f}만원'


def _verify_timelines(data, repo_dir):
    """Prove provided calendar-month counts/first dates against full CSVs.

    Older isolated fixtures may omit a timeline. Once a case declares one,
    its complete 0/6/12/24-month specification and retained yearly exports are
    mandatory: zero observations must stay zero, without interpolation.
    """
    cases = [case for case in data["cases"] if "timeline" in case]
    if not cases:
        return []
    exports = data.get("evidenceExports")
    require(isinstance(exports, list) and exports, "TIMELINE_EXPORTS_MISSING", "월별 표본을 검증할 연간 CSV 목록이 없습니다.")
    yearly, files = {}, set()
    for export in exports:
        path = (repo_dir / export["file"]).resolve()
        year = export["year"]
        require(path.parent == (repo_dir / "housing-story-evidence").resolve()
                and path.suffix.lower() == ".csv" and path not in files
                and type(year) is int and 1 <= year < 9999,
                "TIMELINE_EXPORT_INVALID", "월별 검증용 CSV 경로나 연도가 잘못됐습니다.")
        files.add(path)
        require(path.stat().st_size <= 12_000_000, "TIMELINE_EXPORT_INVALID", "월별 검증용 CSV가 허용 크기를 넘습니다.")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == export.get("sha256")
                and ("bytes" not in export or len(raw) == export["bytes"]),
                "TIMELINE_EXPORT_HASH", "월별 검증용 CSV 해시 또는 크기가 다릅니다.")
        rows = _parse_csv(raw, dt.date(year, 1, 1), dt.date(year, 12, 31))
        require("validRecords" not in export or len(rows) == export["validRecords"],
                "TIMELINE_EXPORT_COUNT", "연간 CSV의 해제 제외 계약 건수가 다릅니다.")
        yearly.setdefault(year, []).extend(rows)
    results = []
    for case in cases:
        timeline = case["timeline"]
        require(isinstance(timeline, list) and len(timeline) == 4
                and {item.get("monthOffset") for item in timeline} == {0, 6, 12, 24},
                "TIMELINE_HORIZONS", "월별 비교는 기사월과 6·12·24개월 뒤 달을 구분해야 합니다.")
        policy = dt.date.fromisoformat(case["policyDate"])
        area = Decimal(case["areaSqm"])
        verified = []
        for item in timeline:
            offset = item["monthOffset"]
            year, zero_month = divmod(policy.year * 12 + policy.month - 1 + offset, 12)
            month = zero_month + 1
            require(year in yearly, "TIMELINE_YEAR_MISSING", "월별 표본 조회 연도의 공식 CSV가 없습니다.")
            candidates = {row for row in yearly[year] if row[0].month == month
                          and row[1] == case["location"] and row[2] == case["apartment"] and row[3] == area}
            require(type(item.get("countInMonth")) is int and item["countInMonth"] == len(candidates),
                    "TIMELINE_MONTH_COUNT", "같은 단지·면적의 해당 월 계약 건수가 원본과 다릅니다.")
            record = item.get("record")
            if not candidates:
                require(record is None and item.get("status") == "no_same_area_transaction_in_official_export",
                        "TIMELINE_ZERO_NOT_PRESERVED", "거래 0건인 달에 개별 아파트 가격을 만들었습니다.")
            else:
                require(isinstance(record, dict), "TIMELINE_RECORD_MISSING", "해당 월의 실거래 표본이 없습니다.")
                selected = (dt.date.fromisoformat(record["date"]), record["address"], record["apartment"],
                            Decimal(record["areaSqm"]), record["priceManwon"], record["floor"])
                require(record.get("cancelled") is False and selected in candidates,
                        "TIMELINE_RECORD_INVALID", "월별 선택 계약이 해제 제외 공식 원본에 없습니다.")
                require(selected[0] == min(row[0] for row in candidates),
                        "TIMELINE_NOT_FIRST_CONTRACT", "최초 거래로 설명한 표본이 해당 월의 첫 계약일이 아닙니다.")
            verified.append({"months": offset, "month": f"{year:04d}-{month:02d}",
                             "officialContractCount": len(candidates),
                             "selectedDate": record["date"] if record else None})
        results.append({"caseId": case["id"], "months": verified})
    return results


def _verify_accounting(case, copied):
    independent = independent_accounting(case)
    production = story.scenario_calculation(case)
    # Reassociation of repeating Decimal divisions may differ below practical
    # units; never accept even one ten-thousandth of a won difference.
    tolerance = Decimal("0.00000001")
    for key, value in independent.items():
        require(key in production and abs(Decimal(production[key]) - Decimal(value)) < tolerance,
                "SCENARIO_ACCOUNTING_MISMATCH", "스토리 회계값이 독립 계산과 다릅니다.")
    required_amounts = (
        "purchase", "sale", "loan", "initialCapital", "acquisitionReserve", "saleReserve",
        "monthlyHoldingBudget", "holdingBudget", "monthlyInterest", "interest",
        "totalCashContributed", "saleEquityBeforeTax", "saleEquityAfterSaleReserveBeforeTax", "illustrativeGainBeforeTax",
        "stressAdditionalMonthlyInterest", "stressSaleEquity",
    )
    for key in required_amounts:
        require(_amount(independent[key]) in copied, "SCENARIO_AMOUNT_MISSING", "독립 계산한 비용·현금흐름이 본문에 없습니다.")
    require(f'{independent["priceChangePct"]:+.2f}%' in copied,
            "OBSERVED_CHANGE_MISMATCH", "실거래 표본 가격 변화율이 다릅니다.")
    labels = ("[실거래 확인]", "[가상 인물·재구성]", "[비용 가정]", "[한계]",
              "창작한 인물", "당시 대출 승인을 뜻하지 않습니다", "세금 미정산 예시 차익",
              "실제 매수인", "동일 호실·동일 소유자", "세후 순이익", "확정 계산하지 않았으므로",
              "현재 같은 집을 사면 같은 결과가 난다는 예측도 아닙니다.", "인과관계", "입증할 수 없습니다")
    require(all(label in copied for label in labels), "FACT_RECONSTRUCTION_GUARD", "실거래·가상 인물·비용·미래 예측의 구분이 빠졌습니다.")
    require(not re.search(r"(?:반드시|무조건|확실히)\s*(?:집값이\s*)?(?:오릅|오른|수익|부자)|수익(?:이|을)?\s*(?:보장합니다|확정됩니다)", copied),
            "FUTURE_GUARANTEE", "확인되지 않은 미래 수익 보장이 포함됐습니다.")
    if independent["illustrativeGainBeforeTax"] <= 0:
        require("예시 차익이 0 이하" in copied, "NEGATIVE_GAIN_GUARD", "비용 차감 후 손실 가능성 안내가 없습니다.")
    for key, ratio in (("대출은 가격의", story.SCENARIO_LOAN_RATIO),
                       ("매수 부대비용 예산을 가격의", story.SCENARIO_ACQUISITION_RESERVE_RATIO)):
        percent = f'{ratio * 100:f}'.rstrip("0").rstrip(".")
        require(key + " " + percent + "%" in copied, "SCENARIO_ASSUMPTION_MISMATCH", "비용 가정 비율과 본문이 다릅니다.")
    return {"unit": "KRW_ten_thousands", "days": independent["days"],
            "values": {key: str(value) for key, value in independent.items()},
            "independentlyCalculated": True, "statutoryTaxesCalculated": False}


def verify_story(repo_dir=DEFAULT_REPO, html_path=None, now=None, started_at=None):
    repo_dir = Path(repo_dir).resolve()
    html_path = Path(html_path) if html_path else repo_dir / "briefing.html"
    now = now or dt.datetime.now(dt.timezone.utc)
    now = now.replace(tzinfo=dt.timezone.utc) if now.tzinfo is None else now
    report = {"schemaVersion": 1, "status": "failed", "checkedAtUtc": now.astimezone(dt.timezone.utc).isoformat(),
              "checks": {}, "errors": [],
              "summary": {"networkAccessed": False, "paidAiInvoked": False,
                          "actualBrowserClipboard": "separate_gate_required"}}
    try:
        evidence_path = repo_dir / "housing-story-evidence.json"
        raw_data = read_json(evidence_path)
        catalog = read_json(repo_dir / "housing-history-articles.json")
        integrity = story.verify_story_evidence(evidence_path, base_dir=repo_dir)
        require(integrity.get("status") == "passed" and integrity.get("caseCount", 0) > 0
                and not integrity.get("errors") and integrity.get("networkUsed") is False
                and integrity.get("paidAiUsed") is False,
                "ACTUAL_CSV_EVIDENCE_INVALID", "공식 CSV 원본과 실거래 표본 대조를 통과하지 못했습니다.")
        for case in raw_data["cases"]:
            for record in case["transactions"]:
                path = (repo_dir / record["evidenceFile"]).resolve()
                require(path.parent == (repo_dir / "housing-story-evidence").resolve(),
                        "EVIDENCE_DIRECTORY", "거래 CSV가 스토리 원본 전용 폴더 밖에 있습니다.")
        report["checks"]["timelineEvidence"] = _verify_timelines(raw_data, repo_dir)
        evidence = story.load_story_evidence(evidence_path, base_dir=repo_dir)
        require(evidence["reviewedAt"] <= now + dt.timedelta(minutes=2),
                "EVIDENCE_REVIEW_TIME", "거래 원본 검토일이 미래입니다.")
        report["checks"].update(actualCsvEvidence=True, evidence=integrity)
        raw_html = html_path.read_text(encoding="utf-8-sig")
        page = BeautifulSoup(raw_html, "html.parser")
        news, news_section = _card(page, "realestate")
        box, section = _card(page, "housingstory")
        require(section is not news_section, "STORY_NEWS_NOT_SEPARATE", "기존 뉴스와 투자 스토리가 같은 카드입니다.")
        copied = box["data-copy-text"]
        require(not SOURCE_LABEL.search(copied) and not SOURCE_LABEL.search(box.get_text()),
                "COPY_SOURCE_LEAK", "자료·출처가 스토리 복사 본문에 포함됐습니다.")
        footer = section.select_one(".source-note")
        require(footer is not None and footer.find_parent(id="housingstory") is None,
                "SOURCE_FOOTER_MISSING", "화면 하단 자료·출처가 없거나 본문 안에 있습니다.")
        footer_urls = [link.get("href") for link in footer.select("a[href]")]
        require(story.PAGE in footer_urls and all(url and url not in copied for url in footer_urls),
                "COPY_SOURCE_URL_LEAK", "공식 자료 주소가 누락됐거나 복사 본문에 섞였습니다.")
        generated = _generation_time(copied, now, started_at)
        selected = story.select_story(news["data-copy-text"], evidence["cases"], catalog["articles"])
        require(selected is not None, "CURRENT_LINKAGE_MISSING", "검증된 과거 기사와 연결할 실거래 사례가 없습니다.")
        current = selected["current"]
        details = [(article["url"], article["title"]) for article in selected["references"]]
        if current:
            require("현재 기사: " + current["title"] in copied and "유사점:" in copied,
                    "CURRENT_LINKAGE_MISMATCH", "현재 기사와 과거 사례의 연결이 다릅니다.")
            details.append((current["url"], "현재 기사: " + current["title"]))
        else:
            require("오늘의 유사 사례로 제시하는 것이 아닌 과거 실거래 참고 사례" in copied
                    and "현재 기사:" not in copied,
                    "REFERENCE_MODE_MISMATCH", "현재 기사 노매치의 참고 사례 구분이 잘못됐습니다.")
        for url, title in details:
            require(url in copied and any(link.get("href") == url and link.get_text() == title
                                         for link in box.select("a[href]")),
                    "ARTICLE_DETAIL_LINK_MISSING", "현재·과거 기사 상세 링크가 화면이나 복사에서 빠졌습니다.")
        report["checks"].update(currentLinkage=True, linkage={"mode": selected["mode"],
                                 "caseId": selected["case"]["id"], "articleDetailCount": len(details)})
        report["checks"]["accounting"] = _verify_accounting(selected["case"], copied)
        report["checks"]["scenarioAccounting"] = True
        expected = story.build_story_section(news["data-copy-text"], _no_network, generated,
                                            catalog_path=repo_dir / "housing-history-articles.json",
                                            evidence_path=evidence_path, evidence_base_dir=repo_dir)
        expected_body, expected_sources = split_content_sources(expected)
        require(copied == expected_body, "STORY_BODY_MISMATCH", "실거래·기사·회계 재현 본문과 복사 내용이 다릅니다.")
        expected_shown = BeautifulSoup(render_history_links(expected_body), "html.parser").decode_contents()
        require(box.decode_contents() == expected_shown, "VISIBLE_COPY_CONTENT_MISMATCH", "화면의 스토리 내용과 복사 내용이 다릅니다.")
        expected_footer = BeautifulSoup(render_sources(expected_sources), "html.parser").decode_contents()
        require(footer.decode_contents() == expected_footer and "자료:" in footer.get_text()
                and "출처:" in footer.get_text(),
                "SOURCE_FOOTER_MISMATCH", "화면 하단 자료·출처가 검증한 출처와 다릅니다.")
        report["checks"]["sourceSeparation"] = True
        report["summary"].update(mode=selected["mode"], caseId=selected["case"]["id"],
                                  htmlSha256=_digest(raw_html), copyTextSha256=_digest(copied),
                                  generatedAtKst=generated.isoformat(), csvCaseCount=integrity["caseCount"])
        report["status"] = "passed"
    except VerificationFailure as error:
        report["errors"].append({"code": error.code, "message": error.message})
    except (OSError, UnicodeError):
        report["errors"].append({"code": "INPUT_READ_FAILED", "message": "스토리 검증 입력 파일을 읽지 못했습니다."})
    except (KeyError, ValueError, TypeError, IndexError, ArithmeticError):
        report["errors"].append({"code": "INPUT_SCHEMA_INVALID", "message": "스토리 검증 입력의 구조 또는 수치가 잘못됐습니다."})
    except Exception:
        # Do not echo arbitrary payloads, credentials or dependency exceptions.
        report["errors"].append({"code": "VALIDATION_INTERNAL_ERROR", "message": "스토리 자동 점검을 완료하지 못했습니다."})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--started-at")
    args = parser.parse_args(argv)
    result = verify_story(args.repo_dir, args.html, started_at=args.started_at)
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        print(json.dumps({"status": "failed", "errorCode": "REPORT_WRITE_FAILED"}))
        return 1
    print(json.dumps({"status": result["status"], "errorCodes": [item["code"] for item in result["errors"]],
                      "caseId": result["summary"].get("caseId")}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
