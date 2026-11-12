"""Financially meaningful offline contracts for the integrated company pipeline."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3

from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

from investment_assistant.single_company.analysis import (
    available_facts, build_report, current_report, financial_metrics, price_metrics, quarterly,
)
from investment_assistant.single_company.calendar import completed_session, next_session, session_dates
from investment_assistant.single_company.config import CompanyConfig
from investment_assistant.single_company.forecast import (
    add_intervals, evaluate, evaluation_id, feature_data, publish_forecast, ridge_fit,
    training_indices,
)
from investment_assistant.single_company.pipeline import ingest
from investment_assistant.single_company.providers import (
    ProviderClient, ProviderError, normalize_prices,
)
from investment_assistant.single_company.storage import BusyJob, CompanyStore
from investment_assistant.web.app import create_app


NOW = datetime(2026, 11, 6, 23, tzinfo=timezone.utc)
CONFIG = CompanyConfig(price_key="price-secret", fred_key="fred-secret",
                       user_agent="Research dev@private.test", usage_record="Private use reviewed",
                       adjustment_record="Known splits and dividends verified")


def bars(start="2025-09-01", end="2026-11-06", multiplier=1):
    return [{"date": day, "open": multiplier * (100 + i), "high": multiplier * (102 + i),
             "low": multiplier * (99 + i), "close": multiplier * (101 + i), "volume": 1000 + i}
            for i, day in enumerate(session_dates(start, end))]


def fact(tag, value, end, start=None, accession="0001045810-26-000001", filed="2026-10-20"):
    value = {"taxonomy": "us-gaap", "tag": tag, "unit": "USD", "val": value,
             "end": end, "filed": filed, "accn": accession, "form": "10-Q"}
    if start:
        value["start"] = start
    return value


class FakeClient:
    request_count = 0
    fail = None
    correction = False
    truncate = False

    def prices(self, symbol, mode, start=None):
        self.request_count += 1
        if self.fail == symbol:
            raise ProviderError("Simulated outage")
        values = bars(multiplier={"NVDA": 1, "SPY": 2, "XLK": 3}[symbol])
        if self.correction and mode == "all":
            # A historic corporate-action correction, outside the ten-session refresh tail.
            values[:20] = [{**b, **{k: b[k] / 2 for k in ("open", "high", "low", "close")}}
                           for b in values[:20]]
        values = [b for b in values if not start or b["date"] >= start]
        return values[-10:] if self.truncate else values, 1

    def submissions(self):
        self.request_count += 1
        return {"cik": 1045810, "tickers": ["NVDA"], "exchanges": ["NASDAQ"],
                "filings": {"recent": {"accessionNumber": ["0001045810-26-000001"],
                "form": ["10-Q"], "acceptanceDateTime": ["2026-10-20T21:00:00Z"],
                "filingDate": ["2026-10-20"], "reportDate": ["2026-09-30"],
                "primaryDocument": ["report.htm"]}}}, 1

    def facts(self):
        self.request_count += 1
        return {"cik": 1045810, "facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
            fact("NetIncomeLoss", 20, "2026-09-30", "2026-07-01")
        ]}}}}}, 1

    def macro(self, series, start):
        self.request_count += 1
        return [{"date": "2026-10-01" if series in {"CPIAUCSL", "UNRATE"} else "2026-11-06",
                 "realtime_start": "2026-11-01", "realtime_end": "9999-12-31", "value": 4}], 1


@pytest.fixture
def saved(tmp_path):
    store = CompanyStore(tmp_path / "company")
    result = ingest(store, CONFIG, client=FakeClient(), now=NOW)
    assert result["status"] == "success"
    return store


def test_exchange_holidays_early_close_and_dst():
    assert completed_session(datetime(2026, 7, 3, 23, tzinfo=timezone.utc)) == "2026-07-02"
    assert completed_session(datetime(2026, 11, 27, 18, 10, tzinfo=timezone.utc)) == "2026-11-25"
    assert completed_session(datetime(2026, 11, 27, 18, 31, tzinfo=timezone.utc)) == "2026-11-27"
    assert completed_session(datetime(2026, 11, 7, 12, tzinfo=timezone.utc)) == "2026-11-06"
    assert next_session("2026-07-02") == "2026-07-06"


def test_prices_reject_quota_duplicate_missing_sessions_and_currency():
    values = bars("2026-11-02", "2026-11-06")
    payload = {"meta": {"symbol": "NVDA", "currency": "USD", "exchange": "NASDAQ", "interval": "1day"},
               "values": [{"datetime": b["date"], **{k: v for k, v in b.items() if k != "date"}} for b in values]}
    assert len(normalize_prices(payload, "NVDA", "USD", "NASDAQ")) == 5
    for invalid in ({**payload, "values": []},
                    {**payload, "values": payload["values"] + payload["values"][:1]},
                    {**payload, "values": payload["values"][:1] + payload["values"][2:]},
                    {**payload, "meta": {**payload["meta"], "currency": "GBP"}},
                    {**payload, "values": [{**payload["values"][0], "close": "NaN"}]}):
        with pytest.raises(ProviderError):
            normalize_prices(invalid, "NVDA", "USD", "NASDAQ")


def test_retry_quota_bodies_auth_failures_and_secret_redaction(tmp_path):
    store = CompanyStore(tmp_path)
    calls, waits = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"}) if len(calls) < 3 else httpx.Response(
            200, json={"observations": [{"date": "2026-11-01"}]})
    client = ProviderClient(CONFIG, store, transport=httpx.MockTransport(handler), sleep=waits.append)
    client.get("fred", "https://api.stlouisfed.org/fred/series/observations", {"api_key": "fred-secret"})
    client.close()
    assert len(calls) == 3
    with store.connect() as db:
        assert "fred-secret" not in db.execute("SELECT redacted_request FROM raw_fetches").fetchone()[0]
    for code, body in ((200, {"status": "error", "message": "key=secret"}), (401, {})):
        client = ProviderClient(CONFIG, store, transport=httpx.MockTransport(
            lambda request: httpx.Response(code, json=body)), sleep=lambda seconds: None)
        with pytest.raises(ProviderError) as exc:
            client.get("twelve", "https://api.twelvedata.com/time_series")
        assert "secret" not in str(exc.value)
        assert client.request_count == 1
        client.close()


def test_hand_calculated_returns_volatility_beta_drawdown_and_volume():
    rows = bars()
    market = [{**b, "close": b["close"] * 2} for b in rows]
    metrics, chart, unusual = price_metrics(rows, market, market)
    assert metrics["return_20"]["value"] == pytest.approx((rows[-1]["close"] / rows[-21]["close"] - 1) * 100)
    assert metrics["relative_market_252"]["value"] == pytest.approx(0)
    assert metrics["beta_252"]["value"] == pytest.approx(1)
    assert metrics["drawdown_252"]["value"] == 0
    assert metrics["volume_ratio"]["value"] == pytest.approx(rows[-1]["volume"] / np.mean([b["volume"] for b in rows[-21:-1]]))
    expected_vol = np.std([b["close"] / a["close"] - 1 for a, b in zip(rows[-21:-1], rows[-20:])], ddof=1) * np.sqrt(252) * 100
    assert metrics["volatility_20"]["value"] == pytest.approx(expected_vol)
    assert len(chart) == len(rows) and not unusual
    short, _, _ = price_metrics(rows[:20], market[:20], market[:20])
    assert short["return_20"]["value"] is None


def test_ytd_q4_conversion_and_restated_accession_cutoff():
    tag = "NetCashProvidedByUsedInOperatingActivities"
    q1 = fact(tag, 10, "2026-03-31", "2026-01-01")
    ytd = fact(tag, 25, "2026-06-30", "2026-01-01")
    nine = fact(tag, 40, "2026-09-30", "2026-01-01")
    annual = fact(tag, 60, "2026-12-31", "2026-01-01", filed="2027-02-01")
    assert [v["val"] for v in quarterly([q1, ytd, nine, annual], "operating_cash_flow")] == [10, 15, 15, 20]
    incompatible = {**ytd, "accn": "0001045810-26-000002"}
    assert len(quarterly([q1, incompatible], "operating_cash_flow")) == 1
    restated = {**q1, "val": 12, "filed": "2026-11-06", "accn": "0001045810-26-000003"}
    at_cutoff = available_facts([q1, restated], [], "2026-11-06")
    assert len(at_cutoff) == 1
    assert quarterly(at_cutoff, "operating_cash_flow")[0]["val"] == 10
    later = available_facts([q1, restated], [], "2026-11-09")
    assert quarterly(later, "operating_cash_flow")[0]["val"] == 12


def test_missing_debt_negative_earnings_and_shares_periods_are_not_zero():
    facts = [fact("CashAndCashEquivalentsAtCarryingValue", 100, "2026-09-30")]
    metrics = financial_metrics(facts, [], "2026-11-06", 100)
    assert metrics["cash"]["value"] == 100
    assert metrics["net_debt"]["value"] is None
    assert metrics["pe"]["value"] is None
    assert metrics["revenue"]["value"] is None


def test_idempotence_corporate_action_reconciliation_failure_and_restore(saved, tmp_path):
    before = saved.latest_dataset("prices", "NVDA", "all")["version"]
    with saved.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM price_bars").fetchone()[0]
    ingest(saved, CONFIG, client=FakeClient(), now=NOW)
    old = saved.latest_snapshot("NVDA")
    with saved.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM price_bars").fetchone()[0] == count
    broken = FakeClient()
    broken.fail = "NVDA"
    assert ingest(saved, CONFIG, client=broken, now=NOW)["status"] == "failed"
    assert saved.latest_snapshot("NVDA") == old
    assert current_report(saved, CONFIG, now=NOW)["data_status"] == "partial"
    truncated = FakeClient()
    truncated.truncate = True
    assert ingest(saved, CONFIG, full=True, client=truncated, now=NOW)["status"] == "failed"
    assert saved.latest_snapshot("NVDA") == old
    revised = FakeClient()
    revised.correction = True
    ingest(saved, CONFIG, full=True, client=revised, now=NOW)
    assert saved.latest_dataset("prices", "NVDA", "all")["version"] != before
    with saved.connect() as db:
        assert db.execute("SELECT COUNT(DISTINCT dataset_version) FROM price_bars WHERE security_id='NVDA' AND adjustment_mode='all'").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM analysis_snapshots").fetchone()[0] >= 2
    destination = saved.backup(tmp_path / "restored.sqlite3")
    with sqlite3.connect(destination) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT COUNT(*) FROM analysis_snapshots").fetchone()[0] >= 2
    with saved.lease(), pytest.raises(BusyJob):
        with saved.lease():
            pass


def test_macro_vintages_and_no_new_daily_observation(saved):
    observations = [{"date": "2026-10-01", "realtime_start": "2026-10-15", "realtime_end": "2026-11-07", "value": 3},
                    {"date": "2026-10-01", "realtime_start": "2026-11-08", "realtime_end": "9999-12-31", "value": 4}]
    saved.save_dataset("macro", "UNRATE", observations, 1)
    report = build_report(saved, CONFIG, "2026-11-06")
    assert report["macro"]["UNRATE"]["value"] == 3
    assert report["macro"]["UNRATE"]["observation_date"] == "2026-10-01"
    assert report["macro"]["UNRATE"]["vintage_start"] == "2026-10-15"
    assert current_report(saved, CONFIG, datetime(2026, 11, 10, 23, tzinfo=timezone.utc))["data_status"] == "stale"
    assert current_report(saved, replace(CONFIG, sector="QQQ"), NOW)["data_status"] == "unavailable"


def test_integrated_routes_stale_unavailable_and_bounded_ranges(saved, tmp_path, monkeypatch):
    from investment_assistant.single_company import web
    monkeypatch.setattr(web, "CompanyConfig", type("Configured", (), {"from_environment": lambda: CONFIG}))
    monkeypatch.setattr(web, "CompanyStore", lambda root: saved)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/company")
        assert response.status_code == 200
        assert "NVIDIA" in response.text and "Forecast not validated" in response.text
        assert "price-secret" not in response.text and "fred-secret" not in response.text
        assert client.get("/api/company").json()["company"]["ticker"] == "NVDA"
        assert len(client.get("/api/prices?range=1m").json()["rows"]) == 21
        assert client.get("/api/prices?range=all").status_code == 422
        assert "chart" not in client.get("/api/analysis/latest").json()
        assert client.get("/api/health").json()["status"] == "ok"
    empty = CompanyStore(tmp_path / "empty")
    monkeypatch.setattr(web, "CompanyStore", lambda root: empty)
    with TestClient(create_app(tmp_path / "other")) as client:
        assert "No analysis has been ingested" in client.get("/company").text
        assert client.get("/api/prices").json()["rows"] == []


def synthetic_chart(n=1550):
    rng = np.random.default_rng(42)
    returns = rng.normal(.0003, .012, (n, 3))
    prices = 100 * np.cumprod(1 + returns, axis=0)
    import pandas as pd
    days = pd.bdate_range("2020-01-01", periods=n)
    return [{"date": d.date().isoformat(), "company": float(p[0]), "market": float(p[1]),
             "sector": float(p[2])} for d, p in zip(days, prices)]


def test_forecast_purge_scaler_feature_availability_and_interval_delay():
    chart = synthetic_chart()
    x, y, origins = feature_data(chart)
    train = training_indices(origins, 900)
    assert max(origins[train] + 20) < 900
    model = ridge_fit(x[train], y[train], 1)
    assert model["mean"] == pytest.approx(x[train].mean(axis=0))
    changed = [dict(r) for r in chart]
    for row in changed[1000:]:
        row["company"] *= 5
    x2, _, _ = feature_data(changed)
    assert np.array_equal(x[origins < 1000], x2[origins < 1000])
    rows = [{"origin_index": i, "prediction": 0, "actual": i / 100} for i in range(100)]
    add_intervals(rows)
    assert "lower" not in rows[79] and "lower" in rows[80]
    assert rows[80]["upper"] < rows[80]["actual"]


def test_frozen_walk_forward_returns_baselines_and_independent_counts():
    assert evaluate(synthetic_chart(500))[0]["status"] == "ineligible"
    report, artifacts = evaluate(synthetic_chart())
    assert report["status"] in {"not_validated", "passed"}
    assert report["scores"]["holdout"]["samples"] == 252
    assert report["scores"]["holdout_nonoverlapping"]["samples"] == 13
    assert set(report["scores"]["holdout"]["mae"]) == {"prediction", "no_change", "historical_mean"}
    assert max(r["target_end"] for r in artifacts["development_predictions"]) < report["holdout_start"]
    assert all(m["training_cutoff"] < m["month"] + "-31" for m in artifacts["monthly_models"])
    json.dumps(report, allow_nan=False)
    json.dumps(artifacts, allow_nan=False)


def test_forecast_publication_requires_pass_and_preserves_published_prediction(tmp_path):
    import hashlib
    from investment_assistant.single_company.storage import encode, utc_now
    chart = synthetic_chart()
    report, artifact = evaluate(chart)
    report.update(status="passed", history_sessions=len(chart),
                  history_hash=hashlib.sha256(encode(chart).encode()).hexdigest())
    store = CompanyStore(tmp_path)
    identity = evaluation_id(CONFIG)
    with store.connect() as db:
        db.execute("INSERT INTO forecast_evaluations VALUES(?,?,?,?,?)",
                   (identity, utc_now(), "{}", encode(report), encode(artifact)))
    snapshot = {"chart": chart, "as_of": chart[-1]["date"], "data_status": "partial",
                "forecast": {"status": "not_validated"}, "input_versions": {}}
    publish_forecast(store, CONFIG, snapshot)
    assert snapshot["forecast"]["status"] == "not_validated"
    snapshot["data_status"] = "current"
    publish_forecast(store, CONFIG, snapshot)
    first = snapshot["forecast"]["prediction"]
    assert snapshot["forecast"]["status"] == "validated_experiment"
    publish_forecast(store, CONFIG, snapshot)
    assert snapshot["forecast"]["prediction"] == first
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM forecast_results").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM forecast_models").fetchone()[0] == 1
    snapshot["chart"][0]["company"] *= .5
    snapshot["forecast"] = {"status": "not_validated"}
    publish_forecast(store, CONFIG, snapshot)
    assert snapshot["forecast"]["status"] == "not_validated"


def test_schedule_uses_new_york_time_and_avoids_duplicate_completed_sessions(saved):
    from investment_assistant.single_company.schedule import scheduled_run
    # 19:00 London = 14:00 New York in November: outside either ingestion window.
    assert scheduled_run(saved, CONFIG, datetime(2026, 11, 6, 19, tzinfo=timezone.utc))["status"] == "skipped"
    # 03:30 UTC = 22:30 New York on the preceding local date.
    assert scheduled_run(saved, CONFIG, datetime(2026, 11, 7, 3, 30, tzinfo=timezone.utc))["reason"] == "Expected completed session already ingested"


def test_financial_retry_survives_submissions_update(saved):
    calls = []
    client = FakeClient()
    original = client.facts

    def retried_facts():
        calls.append(True)
        return original()

    client.facts = retried_facts
    # A newly accepted accession may already have been saved when its facts fetch failed.
    original_submissions = client.submissions

    def recent_submissions():
        payload, fetch = original_submissions()
        payload["filings"]["recent"]["acceptanceDateTime"] = ["2026-11-06T10:00:00Z"]
        payload["filings"]["recent"]["filingDate"] = ["2026-11-06"]
        return payload, fetch

    client.submissions = recent_submissions
    from investment_assistant.single_company.providers import normalize_submissions
    payload, fetch = client.submissions()
    saved.save_dataset("filings", CONFIG.cik, normalize_submissions(payload, CONFIG), fetch)
    with saved.connect() as db:
        db.execute("UPDATE datasets SET fetched_at='2026-11-05T19:00:00+00:00' WHERE kind='facts'")
    assert ingest(saved, CONFIG, client=client, now=NOW)["status"] == "success"
    assert len(calls) == 1


def test_realized_forecasts_use_session_dates_and_matching_company(saved):
    identity = evaluation_id(CONFIG)
    with saved.connect() as db:
        for key, evaluation in (("live:current", identity), ("live:other", "another-company")):
            db.execute("INSERT INTO forecast_results VALUES(?,?,?,?,?,?,?,?,?)",
                       (key, evaluation, "2026-10-02T02:30:00+00:00", "2026-10-01",
                        "2026-10-29", .01, -.02, .04, None))
    assert ingest(saved, CONFIG, client=FakeClient(), now=NOW)["status"] == "success"
    values = {b["date"]: b["close"] for b in bars()}
    with saved.connect() as db:
        outcomes = {r["id"]: r["realized"] for r in db.execute("SELECT id,realized FROM forecast_results")}
    assert outcomes["live:current"] == pytest.approx(values["2026-10-29"] / values["2026-10-01"] - 1)
    assert outcomes["live:other"] is None
