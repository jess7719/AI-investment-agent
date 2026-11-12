"""Offline identity, isolation, durable queue and stored-only web integration contracts."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from investment_assistant.single_company.analysis import build_report, current_report
from investment_assistant.single_company.directory import Directory, MARKET, PILOT, REFERENCE, parse_seed
from investment_assistant.single_company.facade import AnalysisFacade
from investment_assistant.single_company.jobs import claim, enqueue, enqueue_recent, finish, get_job, reserve_credit, work_one
from investment_assistant.single_company.pipeline import ingest
from investment_assistant.single_company.providers import ProviderDeferred
from investment_assistant.single_company.storage import CompanyStore, SCHEMA, encode
from investment_assistant.web.app import create_app
from test_single_company import CONFIG, FakeClient, NOW, bars


@pytest.fixture
def store(tmp_path):
    return CompanyStore(tmp_path / "data" / "single-company")


def sid(store, ticker):
    return next(r["security_id"] for r in Directory(store).search(ticker)["items"] if r["ticker"] == ticker)


def verify(store, *tickers):
    return Directory(store).verify_reference({"data": [
        {"symbol": ticker, "exchange": "NASDAQ", "currency": "USD", "type": "Common Stock"}
        for ticker in tickers]})


class MultiClient(FakeClient):
    def __init__(self, config):
        self.config = config
        self.calls = []

    def prices(self, symbol, mode, start=None):
        self.calls.append(("prices", symbol, mode))
        if self.fail == symbol:
            from investment_assistant.single_company.providers import ProviderError
            raise ProviderError("Fixture outage")
        return [b for b in bars(multiplier={"NVDA": 1, "AAPL": 4, "MSFT": 7, "SPY": 2, "XLK": 3}[symbol])
                if not start or b["date"] >= start], 1

    def submissions(self):
        payload, fetch = super().submissions()
        payload.update(cik=int(self.config.cik), tickers=[self.config.ticker])
        payload["exchanges"] = [self.config.exchange]
        accession = self.config.cik + "-26-000001"
        payload["filings"]["recent"]["accessionNumber"] = [accession]
        self.calls.append(("filings", self.config.cik))
        return payload, fetch

    def facts(self):
        payload, fetch = super().facts()
        payload["cik"] = int(self.config.cik)
        for fact in payload["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]:
            fact["accn"] = self.config.cik + "-26-000001"
        return payload, fetch

    def macro(self, series, start):
        self.calls.append(("macro", series))
        return super().macro(series, start)


def test_sourced_directory_counts_defaults_and_benchmarks(store):
    data = Directory(store).search()
    assert data["counts"]["directory"] >= 500
    assert data["counts"]["analysed"] == 0 and data["counts"]["template_supported"] == 1
    assert len(data["items"]) == 50 and data["next_cursor"]
    assert Directory(store).get(MARKET).identity["instrument_type"] == "benchmark"
    assert not Directory(store).get(sid(store, "JPM")).ready


@pytest.mark.parametrize("query,ticker", [("nvda", "NVDA"), (" APPLE ", "AAPL"), ("BRK-B", "BRK-B"), ("MSFT", "MSFT")])
def test_ranked_case_name_punctuation_and_literal_wildcards(store, query, ticker):
    rows = Directory(store).search(query)["items"]
    assert rows[0]["ticker"] == ticker
    assert not Directory(store).search("%_'")["items"]


def test_share_classes_pagination_and_bound_cursors(store):
    a = Directory(store).get(sid(store, "GOOG"))
    b = Directory(store).get(sid(store, "GOOGL"))
    assert a.issuer_id == b.issuer_id and a.security_id != b.security_id
    first = Directory(store).search("a", limit=30)
    second = Directory(store).search("a", cursor=first["next_cursor"], limit=30)
    assert not {r["security_id"] for r in first["items"]} & {r["security_id"] for r in second["items"]}
    with pytest.raises(ValueError):
        Directory(store).search("b", cursor=first["next_cursor"])
    for cursor in ("broken", "W10=", "bnVsbA=="):
        with pytest.raises(ValueError):
            Directory(store).search(cursor=cursor)
    assert Directory(store).search(filter="saved", saved=[a.security_id])["items"][0]["security_id"] == a.security_id


def test_explicit_provider_alias_search_and_snapshot_cache_are_security_scoped(store):
    a, b = sid(store, "GOOG"), sid(store, "GOOGL")
    with store.connect() as db:
        db.execute("INSERT INTO provider_symbols VALUES(?,?,?,?,?,?,?)",
                   (a, "twelve", "GOOG.TEST", "NASDAQ", "verified", "2026-11-06", "2026-11-06"))
    assert Directory(store).search("goog.test")["items"][0]["security_id"] == a
    report = {"as_of": "2026-11-06", "input_versions": {}, "data_status": "partial"}
    store.save_snapshot(a, report)
    store.save_snapshot(b, report)
    assert store.latest_snapshot(a) == store.latest_snapshot(b) == report
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM analysis_snapshots WHERE security_id IN (?,?)", (a, b)).fetchone()[0] == 2


def test_atomic_directory_import_field_order_shrink_and_ticker_reuse(store):
    payload = json.loads(REFERENCE.read_text(encoding="utf-8"))
    before = sid(store, "AAPL")
    reordered = {"fields": list(reversed(payload["fields"])), "data": [list(reversed(r)) for r in payload["data"]]}
    assert parse_seed(reordered) == parse_seed(payload)
    Directory(store).import_sec(reordered)
    assert sid(store, "AAPL") == before
    tiny = {**payload, "data": payload["data"][:5]}
    with pytest.raises(ValueError, match="shrinking"):
        Directory(store).import_sec(tiny)
    changed = json.loads(json.dumps(payload))
    row = next(r for r in changed["data"] if r[2] == "AAPL")
    row[0] = 999999999
    with pytest.raises(ValueError, match="identity"):
        Directory(store).import_sec(changed)
    assert Directory(store).get(before).identity["cik"] == "0000320193"


def test_retirement_preserves_identity_and_old_snapshot(store):
    payload = json.loads(REFERENCE.read_text(encoding="utf-8"))
    security = sid(store, "AAPL")
    store.save_snapshot(security, {"as_of": "2026-11-06", "input_versions": {}, "data_status": "partial"})
    payload["data"] = [r for r in payload["data"] if r[2] != "AAPL"]
    Directory(store).import_sec(payload)
    assert Directory(store).get(security).identity["status"] == "retired"
    assert store.latest_snapshot(security)["as_of"] == "2026-11-06"
    assert not Directory(store).search("AAPL")["items"]


def test_reference_verification_does_not_grant_financial_template_or_adjustments(store):
    verify(store, "AAPL", "MSFT")
    context = Directory(store).get(sid(store, "AAPL"))
    config = context.config(CONFIG)
    assert context.ready and config.sector == "" and config.adjustment_record == ""
    assert config.financial_template == "unreviewed" and config.cik == "0000320193"
    assert not Directory(store).get(sid(store, "JPM")).ready


def test_three_reports_isolate_prices_models_jobs_and_preserve_nvda_parity(store):
    ingest(store, CONFIG, client=FakeClient(), now=NOW)
    old = current_report(store, CONFIG, NOW)
    verify(store, "AAPL", "MSFT")
    for ticker in ("AAPL", "MSFT"):
        context = Directory(store).get(sid(store, ticker))
        config = context.config(CONFIG)
        assert ingest(store, config, client=MultiClient(config), now=NOW)["status"] == "success"
        report = AnalysisFacade(store, CONFIG).latest(context.security_id)
        assert report["security_id"] == context.security_id and report["company"]["ticker"] == ticker
        assert report["financials"]["pe"]["value"] is None
        assert report["metrics"]["relative_sector_20"]["value"] is None
        assert report["forecast"]["status"] == "not_validated"
        assert all(row["sector"] is None for row in report["chart"])
    assert current_report(store, CONFIG, NOW) == old
    assert store.latest_dataset("prices", "AAPL", "all")["version"] != store.latest_dataset("prices", "MSFT", "all")["version"]
    assert build_report(store, CONFIG, "2026-11-06")["metrics"] == old["metrics"]


def test_shared_benchmarks_macro_and_issuer_filings_are_reused(store):
    verify(store, "AAPL", "MSFT")
    # Align clock-dependent freshness with the pipeline's injected now.
    ingest(store, CONFIG, client=FakeClient(), now=NOW)
    with store.connect() as db:
        db.execute("UPDATE datasets SET fetched_at=?", (NOW.isoformat(),))
        db.execute("UPDATE job_runs SET finished_at=? WHERE job LIKE 'full:%'", (NOW.isoformat(),))
    context = Directory(store).get(sid(store, "AAPL"))
    config = context.config(CONFIG)
    client = MultiClient(config)
    ingest(store, config, client=client, now=NOW)
    assert not any(c[0] == "macro" or c[:2] == ("prices", "SPY") for c in client.calls)


def test_same_ticker_multiple_exchanges_uses_selected_security_key(store):
    original = Directory(store).get(sid(store, "AAPL"))
    with store.connect() as db:
        db.execute("INSERT INTO securities SELECT 'fixture-second-listing',issuer_id,ticker,name,'NYSE',"
                   "currency,share_class,instrument_type,status,effective_from,effective_to,ticker_norm,"
                   "name_norm,'fixture-second-listing',source FROM securities WHERE security_id=?",
                   (original.security_id,))
        db.execute("INSERT INTO analysis_coverage(security_id) VALUES('fixture-second-listing')")
        db.execute("INSERT INTO benchmark_mappings VALUES('fixture-second-listing',?,NULL,'2026-11-06','fixture')", (MARKET,))
    Directory(store).verify_reference({"data": [{"symbol": "AAPL", "exchange": "NYSE", "currency": "USD", "type": "Common Stock"}]})
    context = Directory(store).get("fixture-second-listing")
    config = context.config(CONFIG)
    assert ingest(store, config, client=MultiClient(config), now=NOW)["status"] == "success"
    report = AnalysisFacade(store, CONFIG).latest(context.security_id)
    assert report["security_id"] == context.security_id and report["company"]["exchange"] == "NYSE"
    assert store.latest_snapshot(original.security_id) is None
    with pytest.raises(ValueError, match="Ambiguous"):
        store.security_key("AAPL")


def test_opt_in_scheduling_queues_recent_analysis_and_never_directory_only_entries(store):
    store.save_snapshot(PILOT, {"company": CONFIG.identity, "as_of": "2026-11-05", "input_versions": {}, "data_status": "partial"})
    jobs = enqueue_recent(store, NOW)
    assert len(jobs) == 1 and get_job(store, jobs[0])["security_id"] == PILOT
    verify(store, "AAPL")
    explicit = enqueue(store, sid(store, "AAPL"))
    assert get_job(store, jobs[0])["priority"] < explicit["priority"]
    assert enqueue_recent(store, datetime(2026, 12, 1, tzinfo=timezone.utc)) == []


def test_source_failure_retains_last_good_snapshot_and_scoped_status(store, monkeypatch):
    ingest(store, CONFIG, client=FakeClient(), now=NOW)
    old = store.latest_snapshot(PILOT)
    verify(store, "AAPL")
    config = Directory(store).get(sid(store, "AAPL")).config(CONFIG)
    bad = MultiClient(config)
    bad.fail = "AAPL"
    assert ingest(store, config, client=bad, now=NOW)["status"] == "failed"
    assert store.latest_snapshot(PILOT) == old
    assert current_report(store, CONFIG, NOW)["data_status"] == "current"
    from investment_assistant.single_company import schedule
    monkeypatch.setattr(schedule, "ingest", lambda *a, **k: pytest.fail("Another company's failure rescheduled NVDA"))
    assert schedule.scheduled_run(store, CONFIG, datetime(2026, 11, 7, 3, 31, tzinfo=timezone.utc))["status"] == "skipped"


def test_prepare_deduplicates_claim_recovers_expired_lease_and_blocks_stale_owner(store):
    first = enqueue(store, PILOT)
    assert enqueue(store, PILOT, "refresh")["job_id"] == first["job_id"]
    running = claim(store, now=100)
    assert claim(store, now=101) is None
    recovered = claim(store, now=3701)
    assert recovered["job_id"] == running["job_id"] and recovered["lease_owner"] != running["lease_owner"]
    finish(store, running, "success")
    assert get_job(store, first["job_id"])["state"] == "running"
    finish(store, recovered, "success")
    assert get_job(store, first["job_id"])["state"] == "success"


def test_worker_missing_credentials_waits_without_busy_loop_and_restarts(store):
    enqueue(store, PILOT)
    config = replace(CONFIG, price_key="")
    result = work_one(store, config)
    assert result["state"] == "waiting" and result["retry_after"] > time.time()
    assert work_one(CompanyStore(store.root), config)["state"] == "idle"


def test_credit_budget_accounts_for_weight_retries_and_waits(store, monkeypatch):
    store.queue_claim = (enqueue(store, PILOT)["job_id"], "")
    running = claim(store)
    store.queue_claim = (running["job_id"], running["lease_owner"])
    monkeypatch.setenv("COMPANY_TWELVE_DAILY_CREDITS", "2")
    reserve_credit(store, "twelve", weight=2)
    with pytest.raises(ProviderDeferred) as exc:
        reserve_credit(store, "twelve")
    assert exc.value.retry_after > time.time()
    with store.connect() as db:
        assert db.execute("SELECT used FROM provider_credits WHERE provider='twelve' AND length(day)=10").fetchone()[0] == 2


def test_migration_preserves_snapshot_lineage_forecast_and_restore(tmp_path):
    path = tmp_path / "legacy"
    path.mkdir()
    report = {"company": CONFIG.identity, "as_of": "2026-11-06", "input_versions": {"price": "old-v1"}, "data_status": "partial"}
    with sqlite3.connect(path / "company.sqlite3") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO securities VALUES(?,?)", ("NVDA", encode(CONFIG.identity)))
        db.execute("INSERT INTO analysis_snapshots VALUES(1,?,?,?,?,?,?,?,?,?)",
                   ("NVDA", "2026-11-06", "2026-11-06", "{}", "company-v1", None, encode(report), "partial", "legacy-fingerprint"))
        db.execute("INSERT INTO forecast_evaluations VALUES('frozen','2026-11-06','{}','{}','{}')")
    store = CompanyStore(path)
    assert store.latest_snapshot(PILOT) == report
    assert CompanyStore(path).latest_snapshot("NVDA") == report
    backup = store.backup(tmp_path / "backup.sqlite3")
    restored_root = tmp_path / "restore"
    restored_root.mkdir()
    (restored_root / "company.sqlite3").write_bytes(backup.read_bytes())
    restored = CompanyStore(restored_root)
    assert restored.latest_snapshot(PILOT) == report
    with restored.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=3").fetchone()[0] == 1
        assert db.execute("SELECT id FROM forecast_evaluations").fetchone()[0] == "frozen"


def test_web_gets_are_stored_only_scoped_and_mutations_keep_csrf(store, tmp_path, monkeypatch):
    from investment_assistant.single_company import web
    from investment_assistant.single_company.providers import ProviderClient
    monkeypatch.setattr(web, "CompanyStore", lambda root: store)
    monkeypatch.setattr(ProviderClient, "get", lambda *a, **k: pytest.fail("GET called vendor"))
    app = create_app(tmp_path)
    with TestClient(app) as client:
        page = client.get("/company")
        assert 'id="company-navigator"' in page.text
        assert client.get("/api/securities").json()["counts"]["directory"] >= 500
        aapl = sid(store, "AAPL")
        assert client.get(f"/api/securities/{aapl}/analysis/latest").json()["security_id"] == aapl
        fragment = client.get(f"/api/securities/{aapl}/report").json()
        assert fragment["security_id"] == aapl and "Apple" in fragment["html"] and "NVIDIA" not in fragment["html"]
        assert client.get(f"/api/securities/{aapl}/prices").json()["rows"] == []
        assert client.get(f"/api/securities/{aapl}/prices?range=all").status_code == 422
        assert client.get("/api/securities?q=" + "x" * 101).status_code == 422
        assert client.get("/api/securities/missing/analysis/latest").status_code == 404
        assert "could not be resolved" in client.get("/company?security_id=missing").text
        assert client.post(f"/api/securities/{PILOT}/prepare").status_code == 403
        headers = {"X-CSRF-Token": app.state.csrf, "Origin": "http://testserver"}
        first = client.post(f"/api/securities/{PILOT}/prepare", headers=headers)
        assert first.status_code == 202
        assert client.post(f"/api/securities/{PILOT}/refresh", headers=headers).json()["job_id"] == first.json()["job_id"]
        assert client.get("/api/analysis-jobs/" + first.json()["job_id"]).json()["security_id"] == PILOT
        headers["Origin"] = "https://external.test"
        assert client.post(f"/api/securities/{PILOT}/refresh", headers=headers).status_code == 403


def test_unsupported_preparation_never_queues_and_retired_has_resolution_screen(store, tmp_path, monkeypatch):
    from investment_assistant.single_company import web
    monkeypatch.setattr(web, "CompanyStore", lambda root: store)
    app = create_app(tmp_path)
    aapl = sid(store, "AAPL")
    with TestClient(app) as client:
        client.get("/company")
        assert client.post(f"/api/securities/{aapl}/prepare", headers={"X-CSRF-Token": app.state.csrf}).status_code == 422
        with store.connect() as db:
            db.execute("UPDATE securities SET status='retired' WHERE security_id=?", (aapl,))
        assert client.get(f"/api/securities/{aapl}/analysis/latest").status_code == 410
        assert "retired" in client.get(f"/company?security_id={aapl}").text
