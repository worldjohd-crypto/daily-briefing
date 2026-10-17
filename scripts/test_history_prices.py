import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import history_prices as hp


NOW = dt.datetime(2026, 10, 17, 12, tzinfo=dt.timezone.utc)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def envelope(name, rows, total=None):
    return {name: [{"head": [{"list_total_count": len(rows) if total is None else total},
                             {"RESULT": {"CODE": "INFO-000"}}]}, {"row": rows}]}


def fake_official_get(calls=None, damage=None, basis="기준시점 : 2026.06.=100.0"):
    def get(url, params, **kwargs):
        if calls is not None:
            calls.append((url, dict(params)))
        if url == hp.METADATA_URL:
            return Response(envelope("SttsApiTbl", [{
                "STATBL_ID": hp.TABLE_ID, "STATBL_NM": "(월) 매매가격지수_아파트",
                "DTACYCLE_CD": "MM", "RPSTUI_NM": basis,
            }]))
        rows = [{"STATBL_ID": hp.TABLE_ID, "DTACYCLE_CD": "MM",
                 "CLS_ID": int(params["CLS_ID"]), "ITM_ID": 100001,
                 "GRP_ID": None, "WRTTIME_IDTFR_ID": month, "DTA_VAL": 50 + i * .2}
                for i, month in enumerate(hp._months())]
        if damage:
            damage(rows)
        return Response(envelope("SttsApiTblData", rows, total=len(hp._months())))
    return get


class HistoryPricesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "prices.json"

    def load(self, **kwargs):
        return hp.load_history_prices(cache_path=self.cache, now=NOW,
                                      key=kwargs.pop("key", "test-secret"), **kwargs)

    def test_full_official_series_identity_and_dynamic_basis_are_saved(self):
        calls = []
        data = self.load(get=fake_official_get(calls, basis="기준시점 : 2027.01.=100.0"))
        self.assertEqual(data["status"], "live")
        self.assertIn("2027.01", data["basis"])
        self.assertEqual(data["series"]["서울"]["observed"], 242)
        self.assertEqual(data["series"]["전국"]["class"], "500001")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(p["pSize"] <= 1000 for _, p in calls))
        self.assertNotIn("test-secret", self.cache.read_text(encoding="utf-8"))

    def test_30_day_cache_avoids_repeated_api_downloads(self):
        self.load(get=fake_official_get())
        def forbidden(*args, **kwargs):
            self.fail("Fresh cache should not call official service")
        cached = self.load(get=forbidden)
        self.assertEqual(cached["status"], "cache")
        self.assertFalse(cached["stale"])

    def test_stale_cache_failure_is_labelled_and_exception_key_not_exposed(self):
        self.load(get=fake_official_get())
        stored = json.loads(self.cache.read_text(encoding="utf-8"))
        stored["downloadedAt"] = (NOW - dt.timedelta(days=31)).isoformat()
        self.cache.write_text(json.dumps(stored), encoding="utf-8")
        def failing(*args, **kwargs):
            raise RuntimeError("GET ?KEY=test-secret")
        result = self.load(get=failing)
        self.assertEqual(result["status"], "cache")
        self.assertTrue(result["stale"])
        self.assertEqual(result["refreshReason"], "official_fetch_failed")
        self.assertNotIn("test-secret", json.dumps(result))
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), stored)

    def test_missing_key_never_uses_sample_rows_or_invents_values(self):
        def forbidden(*args, **kwargs):
            self.fail("No key must not pretend sample is a full series")
        result = self.load(get=forbidden, key="")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["series"], {})

    def test_missing_duplicate_or_mixed_series_rows_are_rejected(self):
        mutations = [lambda rows: rows.pop(),
                     lambda rows: rows.__setitem__(-1, dict(rows[0])),
                     lambda rows: rows[0].update(CLS_ID=500009),
                     lambda rows: rows[0].update(DTA_VAL=None)]
        for damage in mutations:
            with self.subTest(damage=damage):
                result = self.load(get=fake_official_get(damage=damage))
                self.assertEqual(result["status"], "unavailable")
                self.assertFalse(self.cache.exists())

    def test_foreign_table_cache_is_not_used_even_when_fresh(self):
        self.load(get=fake_official_get())
        stored = json.loads(self.cache.read_text(encoding="utf-8"))
        stored["table"] = "wrong-table"
        self.cache.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual(self.load(key="")["status"], "unavailable")

    def test_comparison_uses_calendar_month_endpoints_and_index_ratios(self):
        data = self.load(get=fake_official_get())
        result = hp.compare_price_changes(data, "2017-08-02", "서울")
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["base_month"], "201708")
        monthly = data["series"]["서울"]["monthly"]
        for n, month in [(6, "201802"), (12, "201808"), (24, "201908")]:
            row = result["horizons"][n]
            self.assertEqual(row["month"], month)
            self.assertAlmostEqual(row["change_pct"], (monthly[month] / monthly["201708"] - 1) * 100)
        rebased = json.loads(json.dumps(data))
        rebased["series"]["서울"]["monthly"] = {m: v * 3 for m, v in monthly.items()}
        again = hp.compare_price_changes(rebased, "2017-08-02", "서울")
        self.assertAlmostEqual(result["horizons"][24]["change_pct"], again["horizons"][24]["change_pct"])

    def test_unavailable_endpoint_is_not_substituted_with_nearest_month(self):
        data = self.load(get=fake_official_get())
        result = hp.compare_price_changes(data, "2023-01-01", "서울")
        self.assertEqual(result["horizons"][6]["status"], "available")
        self.assertEqual(result["horizons"][12]["status"], "unavailable")
        self.assertIsNone(result["horizons"][24]["change_pct"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(hp.shift_month("200311", 6), "200405")
        self.assertEqual(hp.shift_month("202001", -2), "201911")


if __name__ == "__main__":
    unittest.main()
