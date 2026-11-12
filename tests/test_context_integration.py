"""CLI, persistence, saved web views and deep evidence, entirely offline."""

import json
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from investment_assistant import cli
from investment_assistant.adapters.yfinance_regime import YFinanceRegimeAdapter
from investment_assistant.config import contextual_data_root
from investment_assistant.services import context_cli
from investment_assistant.services.context_artifacts import ContextArtifactStore
from investment_assistant.services.deep_report_service import DeepReportService
from investment_assistant.services.evidence_packages import EvidencePackageStore
from investment_assistant.services.historical_analogues import HistoricalAnalogueService
from investment_assistant.services.research_service import ResearchService
from investment_assistant.web.app import create_app
from test_market_regime import FakeHistory, no_network, trading_dates  # noqa: F401
from test_research_service import FakeMarketData, FakeNews, sample_article


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTMENT_RESEARCH_ROOT", str(tmp_path / "context"))
    data = FakeHistory()
    store = ContextArtifactStore(contextual_data_root())
    return data, store


@pytest.mark.parametrize("command", ["regime", "analogues"])
def test_cli_historical_asof_json_and_no_openai(context, monkeypatch, capsys, command):
    data, store = context
    monkeypatch.setenv("OPENAI_API_KEY", "SENTINEL_DO_NOT_EXPOSE")
    monkeypatch.setattr(context_cli, "build_regime_service", lambda benchmark: data.service())
    as_of = trading_dates()[280]
    args = ["investment-assistant", command]
    if command == "analogues":
        args += ["TEST", "--top", "3"]
    monkeypatch.setattr(sys, "argv", args + ["--as-of", str(as_of)])
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert str(as_of) in output and "No OpenAI request was made." in output
    assert "SENTINEL" not in output
    paths = list(store.root.rglob("*.json"))
    assert paths and all("SENTINEL" not in p.read_text() for p in paths)
    assert all(json.loads(p.read_text())["as_of"] == str(as_of) for p in paths)
    if command == "analogues":
        assert "historical comparisons, not forecasts" in output
        assert "sample 3" in output


def test_cli_invalid_options_and_future_date_no_provider(context, monkeypatch, capsys):
    def forbidden(*args):
        raise AssertionError("invalid date must stop before constructing provider")
    monkeypatch.setattr(context_cli, "build_regime_service", forbidden)
    monkeypatch.setattr(sys, "argv", ["investment-assistant", "regime", "--as-of",
                                    str(date.today() + timedelta(days=1))])
    assert cli.main() == 1
    assert "Could not build market context" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["regime", "--as-of", "bad-date"])


def test_saved_context_filters_future_corrupt_artifacts_and_asset_types(context):
    data, store = context
    as_of = trading_dates()[-1]
    snapshot = data.service().snapshot(as_of)
    path = store.save(snapshot)
    store.save(snapshot.model_copy(update={"as_of": as_of + timedelta(days=1)}))
    bad = path.with_name("9999-12-31.json")
    bad.write_text("not JSON", encoding="utf-8")
    loaded = store.read_context("TEST", as_of)
    assert loaded["market_regime"]["as_of"] == str(as_of)
    assert not any("observations" in e for e in loaded["market_regime"]["rates"]["evidence"])
    assert store.read_context("BRNT.L", as_of)["market_regime"] is None
    with pytest.raises(ValueError):
        store.read_context("../../.env", as_of)


def test_evidence_package_includes_context_without_changing_advice_or_calling_network(context,
                                                                                     tmp_path):
    data, store = context
    day = trading_dates()[-1]
    store.save(data.service().snapshot(day))
    result = HistoricalAnalogueService(data.service()).search("TEST", day, top=3)
    store.save(result)
    research = ResearchService(FakeMarketData(), FakeNews((sample_article(),)),
                               reports_root=tmp_path / "reports", today=day).prepare("TEST")
    assert research.evidence["market_regime"]["evidence_kind"] == "deterministic contextual evidence"
    assert research.evidence["historical_analogues"]["sample_size"] == 3
    deep = DeepReportService(tmp_path / "reports").prepare(research)
    request = json.loads(deep.request_json)
    assert "do not change investment advice" in request["context_notice"]
    assert "not forecasts" in request["historical_analogues"]["notice"]
    saved = EvidencePackageStore(tmp_path / "reports").save(deep)
    restored = EvidencePackageStore(tmp_path / "reports").load(saved.path)
    assert restored.prepared.research.evidence["market_regime"] == research.evidence["market_regime"]


def test_web_panels_and_get_endpoint_are_read_only(context, tmp_path):
    data, store = context
    day = trading_dates()[-1]
    store.save(data.service().snapshot(day))
    store.save(HistoricalAnalogueService(data.service()).search("TEST", day, top=3))
    before = {str(p): p.read_bytes() for p in store.root.rglob("*.json")}
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/market-context/TEST").status_code == 428
        client.get("/onboarding")
        client.headers["x-csrf-token"] = client.app.state.csrf
        assert client.post("/api/onboarding", json={"action": "skip", "step": 0,
                                                    "answers": {}}).status_code == 200
        response = client.get("/research/TEST")
        assert response.status_code == 200
        for text in ("Market Environment", "Historical Context", "similarity", "60 trading days",
                     "20 trading days", "not forecasts", "insufficient historical sample"):
            assert text in response.text
        response = client.get("/api/market-context/TEST")
        assert response.status_code == 200
        assert len(response.json()["historical_analogues"]["matches"]) == 3
        assert client.post("/api/market-context/TEST", json={}).status_code == 405
    assert before == {str(p): p.read_bytes() for p in store.root.rglob("*.json")}


def test_yfinance_adapter_facts_only_units_nan_and_exclusive_end():
    calls = []
    days = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    frame = pd.DataFrame({"Close": [4.0, float("nan"), 4.1],
                          "Volume": [0, 0, float("nan")]}, index=days)
    class Ticker:
        def history(self, **kwargs):
            calls.append(kwargs)
            return frame
    adapter = YFinanceRegimeAdapter(ticker_factory=lambda symbol: Ticker())
    rows = adapter.rates_history("^TNX", date(2024, 1, 2), date(2024, 1, 4))
    assert len(rows) == 2 and rows[-1].value == 4.1
    assert rows[-1].volume is None and rows[-1].unit == "yield percent"
    assert calls[0]["end"] == "2024-01-05"
    assert calls[0]["auto_adjust"] is False and calls[0]["back_adjust"] is False
    assert not hasattr(rows[0], "classification")


def test_live_adapter_excludes_incomplete_current_day():
    today = datetime.now(timezone.utc).date()
    frame = pd.DataFrame({"Close": [100, 200]}, index=pd.to_datetime(
        [today - timedelta(days=1), today]))
    class Ticker:
        def history(self, **kwargs):
            assert kwargs["end"] == str(today)
            return frame
    rows = YFinanceRegimeAdapter(ticker_factory=lambda s: Ticker()).market_history(
        "SPY", today - timedelta(days=10), today)
    assert len(rows) == 1 and rows[0].date < today
