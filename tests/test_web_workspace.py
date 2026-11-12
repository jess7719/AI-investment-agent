"""Offline end-to-end contract checks; never contact a market provider or paid API."""

import json
import time
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi.testclient import TestClient

from investment_assistant.domain.profile import InvestorProfile
from investment_assistant.web.app import create_app, render_sections
from investment_assistant.web.charts import aligned_rows
from investment_assistant.web.profile import effective_profile
from investment_assistant.web.store import WorkspaceStore
from investment_assistant.web.workflow import PRESETS, Workflow, preferences
from test_research_service import FakeMarketData, FakeNews, FakeReasoner, sample_article
from investment_assistant.services.research_service import ResearchService


def research_factory(root):
    def factory(settings):
        return ResearchService(FakeMarketData(), FakeNews((sample_article(),)),
                               root / "reports", date.today())
    return factory


@pytest.fixture
def workspace(tmp_path):
    app = create_app(tmp_path, research_factory=research_factory(tmp_path),
                     reasoner_factory=lambda *args: FakeReasoner())
    with TestClient(app) as client:
        client.get("/onboarding")
        client.headers["x-csrf-token"] = app.state.csrf
        yield app, client


def skip(client):
    assert client.post("/api/onboarding", json={"action": "skip", "step": 0,
                                              "answers": {}}).status_code == 200


def test_onboarding_gate_resume_complete_edit_skip_and_return(workspace):
    app, client = workspace
    assert client.get("/research/NVDA", follow_redirects=False).headers["location"] == "/onboarding"
    assert client.get("/profile", follow_redirects=False).status_code == 303
    assert client.get("/api/search?q=nvda").status_code == 428
    answers = {"goal": "Learning", "depth": "Step by step", "employment": "Prefer not to say"}
    assert client.post("/api/onboarding", json={"step": 3, "answers": answers}).status_code == 200
    assert app.state.store.get("questionnaire")["step"] == 3
    assert '"step": 3' in client.get("/onboarding").text
    assert client.post("/api/onboarding", json={"action": "complete", "step": 5,
                                              "answers": answers}).status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 200
    assert "Learning" in client.get("/profile").text
    skip(client)
    assert app.state.workflow.context("NVDA")["shared_answers"] == {}
    assert client.get("/", follow_redirects=False).status_code == 200


def test_unknown_answers_rejected_and_risk_never_inferred():
    shared = {"personalise": True, "answers": {"goal": "Learning", "horizon": "Over 5 years",
              "income": "Over 100,000", "employment": "Employed", "reaction": "Comfortable with fluctuations"}}
    explicit = InvestorProfile(ticker="NVDA", intended_holding_period="2 years", risk_tolerance="low")
    profile = effective_profile("NVDA", shared, explicit)
    assert profile.intended_holding_period == "2 years"
    assert profile.investment_objective == "Learning"
    assert profile.risk_tolerance == "low"
    assert effective_profile("NVDA", shared, InvestorProfile(ticker="NVDA")).risk_tolerance is None


def test_mutations_require_same_origin_token_and_html_is_safe(workspace):
    app, client = workspace
    assert client.post("/api/onboarding", headers={"origin": "https://evil.example"}, json={}).status_code == 403
    assert client.post("/api/onboarding", headers={"x-csrf-token": "wrong"}, json={}).status_code == 403
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400
    assert client.post("/api/onboarding", json={"answers": {"salary": "123456"}}).status_code == 422
    html = render_sections('## Test\n<script>alert(1)</script>\n[x](javascript:alert(1))')[0]["html"]
    assert "<script>" not in html
    assert 'href="javascript:' not in html
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


def test_existing_reports_accessible_before_questionnaire_and_notes_are_versioned(tmp_path):
    path = tmp_path / "reports" / "2026-01-01" / "NVDA.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Saved report\n## Overview\nA source-grounded observation.", encoding="utf-8")
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/history").status_code == 200
        identity = app.state.store.all("reports")[0]["id"]
        assert client.get(f"/reports/{identity}").status_code == 200
        client.headers["x-csrf-token"] = app.state.csrf
        response = client.post(f"/api/reports/{identity}/notes", json={
            "section": "report", "text": "My own view", "included": False})
        assert response.status_code == 200
        assert app.state.workflow.context("NVDA")["selected_notes"] == []
        path.write_text("# A new report version", encoding="utf-8")
        client.get("/history")
        assert len(app.state.store.all("reports")) == 2
        assert app.state.store.all("notes")[0]["report_id"] == identity


def test_chart_dates_rebased_on_intersection_and_invalid_prices_excluded():
    rows = aligned_rows(
        [{"date": "2026-01-01", "close": 50}, {"date": "2026-01-02", "close": 100},
         {"date": "2026-01-03", "close": 110}, {"date": "2026-01-04", "close": 0}],
        [{"date": "2026-01-02", "close": 200}, {"date": "2026-01-03", "close": 180},
         {"date": "2026-01-04", "close": 190}])
    assert [row["date"] for row in rows] == ["2026-01-02", "2026-01-03"]
    assert rows[0]["return"] == rows[0]["benchmark_return"] == 0
    assert rows[-1]["return"] == 10
    assert rows[-1]["benchmark_return"] == -10


@pytest.mark.parametrize("preset", list(PRESETS))
def test_all_presets_prepare_immutable_context_without_paid_call(workspace, preset):
    app, client = workspace
    skip(client)
    response = client.post("/api/prepare/SPCX", json={"preset": preset})
    assert response.status_code == 200
    prepared = app.state.store.read("preparations", response.json()["id"])
    assert prepared["preferences"]["preset"] == preset
    request = json.loads(__import__('pathlib').Path(prepared["package_path"]).read_text(encoding="utf-8"))
    payload = json.loads(request["deep_request"]["request_json"])
    assert "learning_context" in payload
    assert "Evidence, risks" in payload["presentation_request"]["instruction"]
    assert app.state.store.all("jobs") == []
    assert "Personal context that will be sent" in client.get(response.json()["url"]).text


def test_note_opt_in_and_profile_changes_require_fresh_evidence(workspace):
    app, client = workspace
    skip(client)
    prepared = app.state.workflow.prepare("SPCX", preferences({}))
    assert not app.state.workflow.is_stale(prepared)
    assert client.post("/api/profile/SPCX", json={"intended_holding_period": "10 years"}).status_code == 200
    response = client.post(f"/api/generate/{prepared['id']}", json={"confirm_paid": True})
    assert response.status_code == 409
    app.state.store.put("reports", {"id": "version-a", "ticker": "SPCX", "markdown": "## Overview\nA view.",
                                   "preferences": {}, "source": "Test", "created_at": "today", "title": "Test"})
    prepared = app.state.workflow.prepare("SPCX", preferences({}))
    client.post("/api/reports/version-a/notes", json={"section": "report", "text": "private", "included": False})
    assert not app.state.workflow.is_stale(prepared)
    client.post("/api/reports/version-a/notes", json={"section": "report", "text": "selected", "included": True})
    assert app.state.workflow.is_stale(prepared)
    assert app.state.workflow.context("SPCX")["selected_notes"][0]["text"] == "selected"
    assert "User commentary" in app.state.workflow.context("SPCX")["selected_notes"][0]["label"]


def test_generation_duplicate_clicks_and_local_preferences_never_rebill(workspace):
    app, client = workspace
    skip(client)
    prepared = app.state.workflow.prepare("SPCX", preferences({}))
    assert client.post(f"/api/generate/{prepared['id']}", json={}).status_code == 422
    response = client.post(f"/api/generate/{prepared['id']}", json={"confirm_paid": True})
    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    repeat = client.post(f"/api/generate/{prepared['id']}", json={"confirm_paid": True}).json()
    assert repeat["created"] is False and repeat["job"]["id"] == job_id
    for _ in range(100):
        job = app.state.store.read("jobs", job_id)
        if job["status"] not in {"queued", "running"}:
            break
        time.sleep(.02)
    assert job["status"] == "completed", job
    refreshed = client.get("/research/SPCX?tab=evidence").text
    assert "Generation completed" in refreshed and "Open saved report" in refreshed
    assert "No paid request has been made for this preparation" not in refreshed
    identity = job["report_id"]
    assert client.get(f"/reports/{identity}").status_code == 200
    response = client.post(f"/api/reports/{identity}/preferences", json={"depth": "Concise", "sections": []})
    assert response.json()["paid_request"] is False
    assert len(app.state.store.all("jobs")) == 1
    assert len(list((app.state.store.root / "reports" / "web-receipts").glob("*.json"))) == 1
    client.get("/history")
    assert len(app.state.store.all("reports")) == 1


@pytest.mark.parametrize("preset", ["news", "deep"])
def test_news_and_deep_generation_use_existing_validation_and_renderers(tmp_path, preset):
    from test_deep_report import prepared_research, deep_report_data
    from test_daily_news_report import report_data
    from investment_assistant.domain.deep_report import DeepResearchReport, GeneratedDeepResearchReport
    from investment_assistant.domain.news_report import DailyNewsReport, GeneratedDailyNewsReport
    from investment_assistant.domain.research import TokenUsage

    research = replace(prepared_research(), report_date=date.today())

    class Reasoner:
        def generate(self, request):
            payload = json.loads(request)
            assert payload["presentation_request"]["language"] == "English"
            if preset == "deep":
                return GeneratedDeepResearchReport(
                    report=DeepResearchReport.model_validate(deep_report_data()),
                    model="gpt-test", usage=TokenUsage(100, 200, 300))
            return GeneratedDailyNewsReport(
                report=DailyNewsReport.model_validate(report_data()),
                model="gpt-test", usage=TokenUsage(100, 200, 300))

    store = WorkspaceStore(tmp_path)
    workflow = Workflow(store, lambda settings: type("Service", (), {
        "prepare": lambda self, ticker: research})(), lambda *args: Reasoner())
    prepared = workflow.prepare("SPCX", preferences({"preset": preset}))
    job, _ = store.claim(prepared)
    workflow.run(job)
    job = store.read("jobs", job["id"])
    assert job["status"] == "completed", job
    saved = store.read("reports", job["report_id"])
    assert saved["preferences"]["preset"] == preset
    assert (tmp_path / "reports" / date.today().isoformat() / f"SPCX-{preset}.md").exists()


def test_chart_service_preserves_quote_units_and_does_not_forward_fill():
    import pandas as pd
    from investment_assistant.web.charts import ChartService
    from types import SimpleNamespace

    prices = pd.DataFrame({"Close": [10, 20, 30], "Volume": [100, 200, 300]},
                          index=pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-22"]))
    comparison = prices.iloc[[1, 2]]
    calls = []

    def factory(ticker):
        def history(**kwargs):
            calls.append((ticker, kwargs))
            return comparison if ticker == "^FTSE" else prices
        return SimpleNamespace(history=history, history_metadata={"currency": "GBp"})

    service = ChartService(factory)
    result = service.get("ABC.L", "1M")
    assert result["currency"] == "GBp"
    assert result["benchmark_symbol"] == "^FTSE"
    assert result["comparison"][0]["date"] == "2026-10-21"
    assert result["comparison"][0]["return"] == 0
    assert all(kwargs["auto_adjust"] is True for _, kwargs in calls)
    service.get("ABC.L", "1M")
    assert len(calls) == 2  # Cached chart range does not refetch.
    assert ChartService._rows(prices.drop(columns=["Volume"]))[0]["volume"] is None


def test_atomic_claim_and_recovery_never_automatically_retry(tmp_path):
    store = WorkspaceStore(tmp_path)
    prepared = {"id": "p", "fingerprint": "same-evidence", "ticker": "NVDA"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.claim(prepared), range(8)))
    assert sum(created for _, created in results) == 1
    assert len({job["id"] for job, _ in results}) == 1
    store.recover_jobs()
    assert store.all("jobs")[0]["status"] == "interrupted"
    assert store.claim(prepared)[1] is False


def test_timeout_retains_ambiguous_attempt_and_rejected_response_retained(tmp_path):
    class Timeout:
        def generate(self, request):
            raise TimeoutError("Do not expose a personal answer here")

    store = WorkspaceStore(tmp_path)
    workflow = Workflow(store, research_factory(tmp_path), lambda *args: Timeout())
    prepared = workflow.prepare("SPCX", preferences({}))
    job, _ = store.claim(prepared)
    workflow.run(job)
    assert store.read("jobs", job["id"])["status"] == "interrupted"
    assert "personal answer" not in store.read("jobs", job["id"])["message"]
    assert list((tmp_path / "reports").glob("*/pending/*.json"))
    # Fresh content gets a separate explicit attempt, with a retained invalid receipt.
    workflow.reasoner_factory = lambda *args: FakeReasoner("[News 99]")
    prepared = workflow.prepare("SPCX", preferences({"depth": "Concise"}))
    job, _ = store.claim(prepared)
    workflow.run(job)
    assert store.read("jobs", job["id"])["status"] == "rejected"
    assert (tmp_path / "reports" / "web-receipts" / f"{job['id']}.json").exists()


def test_web_cli_stays_loopback(monkeypatch):
    from investment_assistant import cli
    import uvicorn
    calls = []
    monkeypatch.setattr("sys.argv", ["investment-assistant", "web"])
    monkeypatch.setattr("investment_assistant.web.app.create_app", lambda: "test-app")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert cli.main() == 0
    assert calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 8000
    assert calls[0]["access_log"] is False


def test_company_search_uses_provider_and_filters_unsafe_tickers(tmp_path):
    app = create_app(tmp_path, search_provider=lambda query: [
        {"symbol": "AMZN", "shortname": "Amazon.com, Inc.", "quoteType": "EQUITY"},
        {"symbol": "../../private", "shortname": "Invalid"}])
    with TestClient(app) as client:
        client.get("/onboarding")
        client.headers["x-csrf-token"] = app.state.csrf
        skip(client)
        results = client.get("/api/search?q=Amazon").json()
        assert results[0]["ticker"] == "AMZN"
        assert all(r["ticker"] != "../../PRIVATE" for r in results)
