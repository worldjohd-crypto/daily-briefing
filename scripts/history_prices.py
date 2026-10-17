"""Free official monthly apartment price indices for historical comparisons.

This module never invokes the briefing generator or an AI service.  A complete
regional series is fetched with the existing R-ONE key, then reused for 30 days.
Only the same table, item, cycle and region may be combined in a calculation.
"""

import datetime as dt
import json
import math
import os
import re
from pathlib import Path

import requests


API_URL = "https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do"
METADATA_URL = "https://www.reb.or.kr/r-one/openapi/SttsApiTbl.do"
TABLE_URL = "https://www.reb.or.kr/r-one/portal/stat/easyStatPage/A_2024_00045.do"
METHOD_URL = "https://www.reb.or.kr/reb/cm/cntnts/cntntsView.do?cntntsId=1033&mi=10333"
TABLE_ID = "A_2024_00045"
CYCLE = "MM"
ITEM_ID = "100001"
REGIONS = {"서울": "500008", "전국": "500001"}
START_MONTH = "200311"
END_MONTH = "202312"
CACHE_FILE = "history-price-cache.json"
MAX_AGE = dt.timedelta(days=30)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DailyBriefingOfficialStats/1.0)"}


def shift_month(month, count):
    """Return YYYYMM shifted by calendar months, without day approximations."""
    if not re.fullmatch(r"\d{6}", str(month)):
        raise ValueError("Invalid year-month")
    year, mon = int(month[:4]), int(month[4:])
    if not 1 <= mon <= 12 or year < 1:
        raise ValueError("Invalid year-month")
    absolute = year * 12 + mon - 1 + count
    year, zero_mon = divmod(absolute, 12)
    if year < 1 or year > 9999:
        raise ValueError("Invalid year-month")
    return f"{year:04d}{zero_mon + 1:02d}"


def _months():
    month = START_MONTH
    result = []
    while month <= END_MONTH:
        result.append(month)
        month = shift_month(month, 1)
    return result


def _utc_now(now):
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(dt.timezone.utc)


def _timestamp(raw):
    parsed = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp timezone missing")
    return parsed.astimezone(dt.timezone.utc)


def _positive_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def _valid_cache(data):
    if not isinstance(data, dict):
        return False
    identity = {
        "schemaVersion": 1, "source": API_URL, "metadataSource": METADATA_URL,
        "table": TABLE_ID, "cycle": CYCLE, "item": ITEM_ID,
        "startMonth": START_MONTH, "endMonth": END_MONTH,
    }
    if any(data.get(key) != value for key, value in identity.items()):
        return False
    try:
        _timestamp(data["downloadedAt"])
        expected = set(_months())
        for region, cls_id in REGIONS.items():
            series = data["series"][region]
            if str(series["class"]) != cls_id or series["observed"] != len(expected):
                return False
            monthly = series["monthly"]
            if set(monthly) != expected or not all(_positive_number(x) for x in monthly.values()):
                return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _read_cache(path):
    try:
        cached = json.loads(Path(path).read_text(encoding="utf-8"))
        return cached if _valid_cache(cached) else None
    except (OSError, ValueError, TypeError):
        return None


def _api_rows(get, url, params, name):
    # Exceptions are intentionally discarded: requests errors can contain the
    # prepared URL, including KEY.  Callers only receive fixed error labels.
    response = get(url, params=params, headers=HEADERS, timeout=25)
    response.raise_for_status()
    payload = response.json()
    blocks = payload.get(name)
    if not isinstance(blocks, list) or len(blocks) != 2:
        raise ValueError("Official API response unavailable")
    head = blocks[0]["head"]
    if not any(x.get("RESULT", {}).get("CODE") == "INFO-000" for x in head):
        raise ValueError("Official API response unavailable")
    total = next(x["list_total_count"] for x in head if "list_total_count" in x)
    if isinstance(total, bool) or not isinstance(total, int) or total < 1:
        raise ValueError("Official API response unavailable")
    rows = blocks[1]["row"]
    if not isinstance(rows, list):
        raise ValueError("Official API response unavailable")
    return total, rows


def _fetch(get, key, now):
    _, metadata = _api_rows(get, METADATA_URL, {
        "KEY": key, "Type": "json", "STATBL_ID": TABLE_ID,
        "pIndex": 1, "pSize": 1000,
    }, "SttsApiTbl")
    if len(metadata) != 1 or metadata[0].get("STATBL_ID") != TABLE_ID:
        raise ValueError("Official table metadata unavailable")
    meta = metadata[0]
    if meta.get("STATBL_NM") != "(월) 매매가격지수_아파트" or meta.get("DTACYCLE_CD") != CYCLE:
        raise ValueError("Official table metadata changed")
    basis = str(meta.get("RPSTUI_NM") or "").strip()
    # The period is read from the official metadata.  No assumed rebasing date.
    if not re.search(r"\d{4}\s*[.년/-]\s*\d{1,2}.*100", basis):
        basis = "기준시점 표기 미확인 · 같은 공식 시계열 내 지수비로 계산"
    result = {
        "schemaVersion": 1, "source": API_URL, "metadataSource": METADATA_URL,
        "table": TABLE_ID, "cycle": CYCLE, "item": ITEM_ID,
        "startMonth": START_MONTH, "endMonth": END_MONTH,
        "basis": basis, "tableName": meta["STATBL_NM"],
        "downloadedAt": now.isoformat(), "series": {},
    }
    expected = set(_months())
    for region, cls_id in REGIONS.items():
        monthly = {}
        total = None
        page = 1
        while total is None or (page - 1) * 1000 < total:
            count, rows = _api_rows(get, API_URL, {
                "KEY": key, "Type": "json", "pIndex": page, "pSize": 1000,
                "STATBL_ID": TABLE_ID, "DTACYCLE_CD": CYCLE,
                "CLS_ID": cls_id, "ITM_ID": ITEM_ID,
                "START_WRTTIME": START_MONTH, "END_WRTTIME": END_MONTH,
            }, "SttsApiTblData")
            if total is None:
                total = count
                if total != len(expected):
                    raise ValueError("Official monthly coverage incomplete")
            elif total != count:
                raise ValueError("Official monthly coverage changed")
            for row in rows:
                if (row.get("STATBL_ID") != TABLE_ID or row.get("DTACYCLE_CD") != CYCLE
                        or str(row.get("CLS_ID")) != cls_id or str(row.get("ITM_ID")) != ITEM_ID
                        or row.get("GRP_ID") not in (None, "")):
                    raise ValueError("Official series identity changed")
                month = str(row.get("WRTTIME_IDTFR_ID"))
                value = row.get("DTA_VAL")
                if month not in expected or month in monthly or not _positive_number(value):
                    raise ValueError("Official monthly observation invalid")
                monthly[month] = value
            page += 1
        if set(monthly) != expected:
            raise ValueError("Official monthly coverage incomplete")
        result["series"][region] = {
            "class": cls_id, "observed": len(monthly),
            "monthly": dict(sorted(monthly.items())),
        }
    return result


def load_history_prices(get=None, key=None, cache_path=CACHE_FILE, now=None):
    """Read a complete official series or an explicitly labelled prior cache.

    ``get`` can be injected for offline checks.  No credentials or exception
    contents are stored or returned, including when an API call fails.
    """
    now = _utc_now(now)
    cached = _read_cache(cache_path)
    if cached:
        age = now - _timestamp(cached["downloadedAt"])
        if dt.timedelta(0) <= age < MAX_AGE:
            return {**cached, "status": "cache", "stale": False,
                    "note": "한국부동산원 공식 API 직접 수집 자료 · 30일 이내 보관본"}
    key = os.environ.get("RONE_KEY", "") if key is None else key
    reason = "key_missing"
    if key:
        try:
            fetched = _fetch(get or requests.get, key, now)
        except Exception:
            reason = "official_fetch_failed"
        else:
            try:
                target = Path(cache_path)
                target.write_text(json.dumps(fetched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError:
                return {**fetched, "status": "live", "stale": False,
                        "note": "한국부동산원 공식 API 신규 수집 · 보관 파일 저장 실패"}
            return {**fetched, "status": "live", "stale": False,
                    "note": "한국부동산원 공식 API 신규 수집"}
    if cached:
        return {**cached, "status": "cache", "stale": True, "refreshReason": reason,
                "note": "공식 API 재확인 불가 · 아래 수집일의 이전 공식 자료 보관본 사용"}
    return {"status": "unavailable", "stale": False, "refreshReason": reason,
            "note": "한국부동산원 과거 월간 지수 확인 불가 · 수치 미표시", "series": {}}


def _article_month(article_date):
    if isinstance(article_date, (dt.datetime, dt.date)):
        return article_date.strftime("%Y%m")
    raw = str(article_date)
    if re.fullmatch(r"\d{6}", raw):
        shift_month(raw, 0)
        return raw
    return dt.date.fromisoformat(raw[:10]).strftime("%Y%m")


def compare_price_changes(dataset, article_date, region="서울"):
    """Compare article-month index with +6, +12 and +24 calendar months.

    This is an observed nominal apartment index change, not a causal effect or
    a forecast.  Article publication day is intentionally not called a daily
    price observation.
    """
    try:
        month = _article_month(article_date)
    except (TypeError, ValueError):
        return {"status": "unavailable", "reason": "invalid_article_date", "region": region}
    result = {"status": "unavailable", "region": region, "base_month": month,
              "base_index": None, "horizons": {}, "basis": dataset.get("basis", ""),
              "downloadedAt": dataset.get("downloadedAt"),
              "formula": "(비교월 지수 / 기사월 지수 - 1) × 100"}
    if dataset.get("status") not in ("live", "cache") or not _valid_cache(dataset):
        result["reason"] = "official_series_unavailable"
        return result
    if region not in REGIONS:
        result["reason"] = "region_not_supported"
        return result
    monthly = dataset["series"][region]["monthly"]
    base = monthly.get(month)
    result["base_index"] = base
    for horizon in (6, 12, 24):
        target = shift_month(month, horizon)
        value = monthly.get(target)
        entry = {"month": target, "index": value, "change_pct": None,
                 "status": "unavailable"}
        if _positive_number(base) and _positive_number(value):
            entry.update(status="available", change_pct=(value / base - 1) * 100)
        result["horizons"][horizon] = entry
    result["status"] = ("available" if all(x["status"] == "available" for x in result["horizons"].values())
                        else "unavailable")
    if result["status"] == "unavailable":
        result["reason"] = "comparison_month_unavailable"
    return result
