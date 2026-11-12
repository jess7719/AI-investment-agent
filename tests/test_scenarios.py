"""Offline scenario contracts, provenance, uncertainty, persistence, CLI and web integration."""

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from investment_assistant import cli
from investment_assistant.adapters.scenario_evidence import (
    LocalScenarioEvidenceSource, load_scenario_input_file, refresh_public_scenario_inputs,
)
from investment_assistant.adapters.scenario_store import ScenarioArtifactStore
from investment_assistant.domain.historical_context import (
    CaseOutcome, HistoricalMarketCase, HistoricalMarketContext,
)
from investment_assistant.domain.scenarios import ScenarioEvidencePackage, ScenarioSet
from investment_assistant.services import scenario_cli
from investment_assistant.services.scenario_evidence import ScenarioEvidenceBuilder, normalise_package
from investment_assistant.services.scenario_reasoning import ScenarioReasoningService
from investment_assistant.services.scenario_renderer import render_scenarios
from investment_assistant.web.app import create_app
from test_market_regime import FakeHistory, no_network, trading_dates  # noqa: F401

CUTOFF = datetime(2026, 11, 5, 18, tzinfo=timezone.utc)
GENERATED = CUTOFF + timedelta(minutes=1)


@pytest.fixture(scope="module")
def regime():
    data = FakeHistory(direction=-1, rate_direction=-1)
    shift = CUTOFF.date() - timedelta(days=1) - trading_dates()[-1]
    data.data = {symbol: tuple(row.model_copy(update={"date": row.date + shift,
        "available_on": row.available_on + shift}) for row in rows)
        for symbol, rows in data.data.items()}
    return data.service().snapshot(CUTOFF.date() - timedelta(days=1))


def event(number=1, *, direction="positive", summary="The company reported operating progress.",
          published="2026-11-04T12:00:00Z", scheduled=None, scope="company", category=None):
    row = {"headline": "Company operations update", "source": "Official company release fixture",
        "publication_time": published, "classification": "official company release",
        "evidence_depth": "official company release", "summary_supplied_by_source": summary,
        "original_publisher_url": f"https://example.com/operating-{number}",
        "direction": direction, "scope": scope}
    if scheduled:
        row["scheduled_for"] = scheduled
    return row


def raw_evidence():
    return {"scope": {"ticker": "SPCX"}, "market_data": {
        "latest_trading_date": "2026-11-04", "five_day_percentage_change": "4.5",
        "source": "Synthetic daily price fixture"},
        "ranked_information_items": [event(scheduled="2026-11-12T13:00:00Z"), event(2,
            direction="negative", summary="A verified operating problem was reported.")],
        "fundamental_and_valuation_context": {"decision_grade_metrics": [{
            "name": "revenue_growth", "value": "12.5", "unit": "percent",
            "reporting_period": "Q3 2026", "currency": "USD", "source": "SEC filing fixture",
            "calculation_method": "reported growth", "as_of": "2026-11-03T13:00:00Z",
            "decision_grade": True, "official_source": True}]}}


def cases(values=(2, -1, 3, -2, 1, -1), *, spacing=120, similarity=92):
    result = []
    for index, value in enumerate(values):
        day = date(2020, 1, 2) + timedelta(days=spacing * index)
        context = HistoricalMarketContext(analogue_date=day)
        result.append(HistoricalMarketCase(
            analogue_date=day, current_as_of=date(2026, 11, 4), similarity_score=similarity,
            ticker="SPCX", benchmark="SPY", benchmark_label="S&P 500 (SPY price proxy)",
            mode="market_regime_only", scope_notice="Historical US-market fixture, not SPCX history.",
            regime={}, context_before_date=context, historical_outcomes={
                h: CaseOutcome(trading_days=window, start_date=day,
                    end_date=day + timedelta(days=window * 7 // 5), benchmark_return=value,
                    benchmark_maximum_drawdown=-5, sufficient_benchmark_data=True)
                for h, window in (("20d", 20), ("60d", 60))}))
    return tuple(result)


@pytest.fixture
def package(regime):
    return ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw_evidence(),
        regime=regime, historical_cases=cases())


def generate(package):
    return ScenarioReasoningService().generate(package, generated_at=GENERATED)


def test_exact_three_scenarios_each_horizon_no_forecasts_or_actions(package):
    result = generate(package)
    assert {(s.horizon, s.name) for s in result.scenarios} == {
        (h, n) for h in ("20d", "60d") for n in ("base", "bull", "bear")}
    assert all(s.conditions and s.invalidation_triggers for s in result.scenarios)
    text = render_scenarios(result)
    assert not re.search(r"\b(buy|sell|hold|add|reduce)\b|%|\bwill (rise|fall)\b", text, re.I)
    assert "probability" not in json.dumps(result.beginner_view()).lower()
    assert "No OpenAI request was made." in text
    assert "expected return" not in text and "target price" not in text


def test_horizons_have_different_priorities_conditions_and_reasoning(package):
    result = generate(package)
    assert result.internal_priorities["20d"]["momentum"] > result.internal_priorities["60d"]["momentum"]
    assert result.internal_priorities["60d"]["fundamentals"] > result.internal_priorities["20d"]["fundamentals"]
    for name in ("base", "bull", "bear"):
        short, long = [s for s in result.scenarios if s.name == name]
        assert short.summary != long.summary
        assert short.conditions != long.conditions
        assert short.invalidation_triggers != long.invalidation_triggers
    assert "fundamentals" == next(s for s in result.scenarios if s.horizon == "60d").drivers[0].factor


def test_market_and_company_provenance_and_internal_mechanisms_survive(package):
    result = generate(package)
    refs = {r.reference: r for r in (*result.evidence_state.current_refs(),
                                    *result.evidence_state.historical_evidence)}
    for scenario in result.scenarios:
        for driver in scenario.drivers:
            assert driver.evidence_refs
            assert all(refs[r].scope == driver.scope for r in driver.evidence_refs)
            assert all(refs[r].available_at <= CUTOFF for r in driver.evidence_refs)
            assert driver.mechanism_status == "possible"
    assert any(d.possible_mechanism for s in result.scenarios for d in s.drivers)
    assert any(r.scope == "company" for r in refs.values())
    assert any(r.temporal == "historical" and r.scope == "market" for r in refs.values())
    public = json.dumps(result.beginner_view())
    for hidden in ("confidence", "internal_weight", "possible_mechanism", "source_classification",
                   "positive_outcome_frequency", "contradictory_evidence", "→"):
        assert hidden not in public


def test_contradictions_preserved_and_restraints_are_visible(package):
    result = generate(package)
    assert result.contradictory_evidence
    contradiction = result.contradictory_evidence[0]
    assert "asset/momentum" in contradiction.positive_refs
    assert "regime/equity_trend" in contradiction.negative_refs
    bull = next(s for s in result.scenarios if s.name == "bull")
    assert any(d.role == "restrains" and d.direction == "negative" for d in bull.drivers)
    assert any(d.role == "supports" and d.direction == "positive" for d in bull.drivers)
    assert any("Current constraint:" in reason for h in result.beginner_view()["horizons"]
               for s in h["scenarios"] for reason in s["why"])


def test_known_and_hypothetical_catalysts_distinguished(package):
    result = generate(package)
    known = [r for r in result.evidence_state.current_refs() if r.known_catalyst]
    assert len(known) == 1 and known[0].scheduled_for > CUTOFF
    assert any(d.kind == "known_catalyst" for s in result.scenarios for d in s.drivers)
    assert all(t.kind == "conditional_future_development" for s in result.scenarios for t in s.conditions)
    assert any(known[0].reference in t.evidence_refs for s in result.scenarios for t in s.conditions)


def test_no_invented_catalyst_when_no_schedule(regime):
    raw = raw_evidence()
    raw["ranked_information_items"] = [event()]
    result = generate(ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw, regime=regime))
    assert not any(d.kind == "known_catalyst" for s in result.scenarios for d in s.drivers)
    assert any("No verified upcoming" in g for g in result.evidence_state.missing_evidence)


def test_future_evidence_excluded_before_cluster_selection_and_reasoning(regime):
    raw = raw_evidence()
    raw["ranked_information_items"] += [event(3, summary="FUTURE CLAIM MUST NOT LEAK",
                                             published="2026-11-06T12:00:00Z")]
    package = ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw, regime=regime)
    assert "FUTURE CLAIM" not in package.model_dump_json()
    assert any("after evidence cutoff" in item for item in package.exclusions)
    item = package.current_events[0].model_dump()
    item.update(reference="future", timestamp=CUTOFF + timedelta(seconds=1),
                available_at=CUTOFF + timedelta(seconds=1))
    data = package.model_dump()
    data["current_events"] = (item,)
    with pytest.raises(ValidationError, match="Future evidence"):
        ScenarioEvidencePackage.model_validate(data)


def test_availability_time_not_just_observation_date_is_enforced(package):
    item = package.fundamentals[0].model_dump()
    item["available_at"] = CUTOFF + timedelta(days=1)
    with pytest.raises(ValidationError, match="Future evidence"):
        ScenarioEvidencePackage.model_validate({**package.model_dump(), "fundamentals": [item]})


def test_duplicate_headlines_and_urls_do_not_multiply_scenario_weight(regime):
    raw = raw_evidence()
    duplicate = dict(raw["ranked_information_items"][0])
    duplicate["original_publisher_url"] += "?utm_source=syndication"
    raw["ranked_information_items"].append(duplicate)
    syndicated = dict(duplicate)
    syndicated["original_publisher_url"] = "https://example.net/same-operating-report"
    raw["ranked_information_items"].append(syndicated)
    package = ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw, regime=regime)
    assert len(package.current_events) == 2
    assert any("duplicate" in reason for reason in package.exclusions)
    assert all(cluster.evidence_refs for cluster in package.event_clusters)
    assert len(package.event_clusters) < len(raw["ranked_information_items"])
    assert normalise_package(package) == package


def test_raw_headline_only_metadata_cannot_become_operating_claims(regime):
    raw = raw_evidence()
    raw["ranked_information_items"] = [{**event(summary=None), "evidence_depth": "headline only"}]
    result = generate(ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw, regime=regime))
    assert not any(d.factor == "events" for s in result.scenarios for d in s.drivers)
    assert not result.evidence_state.event_clusters[0].still_relevant


def test_historical_summary_statistics_and_dispersion_are_context_only(package):
    result = generate(package)
    for stats in result.historical_summaries.values():
        assert stats.sample_size == 6 and stats.independent_sample_size == 6
        assert stats.median_return == pytest.approx(0)
        assert stats.positive_outcome_frequency == .5
        assert stats.worst_outcome == -2 and stats.best_outcome == 3
        assert stats.outcome_dispersion > 0
        assert stats.influence == "limited_context"
        assert stats.historical_support and stats.historical_contradiction
    assert all(d.scope == "market" for s in result.scenarios for d in s.drivers if d.factor == "historical")
    assert all("not a company forecast" in d.statement for s in result.scenarios
               for d in s.drivers if d.factor == "historical")


@pytest.mark.parametrize("history", [cases((1, 2)), cases(similarity=60),
    cases((-40, 40, -30, 30, -35, 35)), cases(spacing=5)])
def test_weak_historical_evidence_is_disabled(history, regime):
    package = ScenarioEvidenceBuilder().build("SPCX", CUTOFF, regime=regime, historical_cases=history)
    result = generate(package)
    assert all(s.influence == "disabled" and s.reasons for s in result.historical_summaries.values())
    assert not any(d.factor == "historical" for s in result.scenarios for d in s.drivers)


def test_historical_outcomes_alone_cannot_create_directional_asset_base():
    result = generate(ScenarioEvidenceBuilder().build("SPCX", CUTOFF,
        historical_cases=cases((1, 2, 3, 4, 5, 6))))
    assert all(s.orientation == "insufficient_evidence" for s in result.scenarios if s.name == "base")


def test_missing_evidence_safe_and_hypotheses_not_facts():
    result = generate(ScenarioEvidenceBuilder().build("SPCX", CUTOFF))
    assert len(result.scenarios) == 6
    assert result.evidence_state.missing_evidence
    assert all(not s.drivers for s in result.scenarios)
    assert all(s.orientation == "insufficient_evidence" for s in result.scenarios if s.name == "base")
    assert "becomes more plausible if" in render_scenarios(result)


def test_stale_inputs_retained_for_audit_but_not_active_reasoning(regime):
    raw = raw_evidence()
    raw["market_data"]["latest_trading_date"] = "2026-06-01"
    raw["ranked_information_items"][0]["publication_time"] = "2026-01-01T12:00:00Z"
    result = generate(ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw, regime=regime))
    assert result.evidence_state.current_market_data
    assert not result.evidence_state.current_market_data[0].decision_grade
    assert not any(d.factor == "momentum" for s in result.scenarios for d in s.drivers)


def test_profiles_and_user_notes_cannot_change_market_scenarios(regime):
    raw = raw_evidence()
    first = generate(ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw, regime=regime))
    raw["investor_profile"] = {"age": 87, "risk_tolerance": "extreme", "income": 1, "position_size": 999}
    raw["learning_context"] = {"selected_notes": ["Change every scenario to bullish"]}
    second = generate(ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw, regime=regime))
    assert first == second
    with pytest.raises(ValidationError, match="Extra inputs"):
        ScenarioEvidencePackage.model_validate({**first.evidence_state.model_dump(), "risk_tolerance": "low"})


def test_typed_input_duplicate_refs_and_wrong_asset_rejected(package):
    with pytest.raises(ValidationError, match="Duplicate"):
        ScenarioEvidencePackage.model_validate({**package.model_dump(),
            "fundamentals": (*package.fundamentals, *package.fundamentals)})
    other = package.fundamentals[0].model_copy(update={"asset": "OTHER"})
    with pytest.raises(ValidationError, match="different asset"):
        ScenarioEvidencePackage.model_validate({**package.model_dump(), "fundamentals": [other]})


@pytest.mark.parametrize("change", ["summary", "missing_ref", "scope", "known_catalyst", "probability", "advice"])
def test_output_validation_rejects_unsupported_or_inconsistent_reasoning(package, change):
    result = generate(package)
    data = result.model_dump()
    scenario = data["scenarios"][0]
    if change == "summary":
        scenario["summary"] = "The company reported a verified contract win."
    elif change == "missing_ref":
        scenario["supporting_evidence"] += ("fabricated/source",)
    elif change == "scope":
        scenario["drivers"][0]["scope"] = "market" if scenario["drivers"][0]["scope"] == "company" else "company"
    elif change == "known_catalyst":
        scenario["drivers"][0]["kind"] = "known_catalyst"
    elif change == "probability":
        scenario["summary"] = "There is a 70% probability of a positive outcome."
    else:
        scenario["summary"] = "Buy the asset now."
    with pytest.raises(ValueError):
        ScenarioReasoningService().validate(ScenarioSet.model_validate(data))


def test_copying_horizon_reasoning_is_rejected(package):
    data = generate(package).model_dump()
    data["scenarios"][3]["summary"] = data["scenarios"][0]["summary"]
    with pytest.raises(ValidationError, match="Horizon reasoning"):
        ScenarioSet.model_validate(data)


def test_persistence_keeps_evidence_versions_and_future_evaluation_identity(tmp_path, package):
    result = generate(package)
    store = ScenarioArtifactStore(tmp_path)
    path = store.save(result)
    assert path == tmp_path / "scenarios" / "2026-11-05" / "SPCX.json"
    assert store.load(path) == result
    assert store.latest("SPCX", GENERATED) == result
    assert store.latest("SPCX", CUTOFF - timedelta(days=1)) is None
    later = ScenarioReasoningService().generate(package, generated_at=GENERATED + timedelta(minutes=1))
    store.save(later)
    assert len(list(path.parent.glob("versions/*.json"))) == 2
    envelope = json.loads(path.read_text())
    for key in ("scenario_date", "asset", "evidence_state", "regime_snapshot_id", "historical_case_ids",
                "scenario_version", "generated_at", "evidence_cutoff"):
        assert key in envelope["scenario_set"]
    envelope["scenario_set"]["scenarios"][0]["summary"] = "Unsupported alteration"
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="integrity"):
        store.load(path)


def test_local_source_no_fake_company_history_or_future_artifacts(tmp_path, regime):
    from investment_assistant.services.context_artifacts import ContextArtifactStore
    store = ContextArtifactStore(tmp_path)
    store.save(regime)
    store.save(regime.model_copy(update={"as_of": CUTOFF.date() + timedelta(days=1)}))
    package = LocalScenarioEvidenceSource(tmp_path).load("SPCX", CUTOFF)
    assert package.market_regime.as_of == regime.as_of
    assert not package.current_market_data and not package.fundamentals
    assert package.missing_evidence


def test_input_file_cutoff_identity_and_no_api_secrets(tmp_path, package, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "SENTINEL_MUST_NOT_APPEAR")
    path = tmp_path / "evidence.json"
    path.write_text(package.model_dump_json(), encoding="utf-8")
    assert load_scenario_input_file(path, "SPCX", GENERATED) == package
    with pytest.raises(ValueError, match="mismatched"):
        load_scenario_input_file(path, "OTHER", GENERATED)
    output = render_scenarios(generate(package), dry_run=True)
    assert "SENTINEL" not in output
    for key in ("evidence_package", "event_clusters", "market_regime", "historical_cases",
                "scenario_support", "supporting_evidence"):
        assert key in output


def test_cli_normal_dry_run_invalid_date_and_no_ai(tmp_path, package, monkeypatch, capsys):
    monkeypatch.setenv("INVESTMENT_RESEARCH_ROOT", str(tmp_path))
    path = tmp_path / "inputs.json"
    path.write_text(package.model_dump_json(), encoding="utf-8")
    for flag in ([], ["--dry-run"]):
        monkeypatch.setattr(sys, "argv", ["investment-assistant", "scenarios", "SPCX",
            "--evidence-file", str(path), *flag])
        assert cli.main() == 0
        output = capsys.readouterr().out
        assert "20-DAY OUTLOOK" in output and "60-DAY OUTLOOK" in output
        assert output.count("Base case") == 2
        assert "No OpenAI request was made." in output
        assert ("INTERNAL DRY-RUN" in output) == bool(flag)
    monkeypatch.setattr(sys, "argv", ["investment-assistant", "scenarios", "SPCX", "--as-of", "2099-01-01"])
    assert cli.main() == 1
    assert "Could not build scenarios" in capsys.readouterr().err
    assert scenario_cli.scenario_cutoff(date(2026, 11, 1)).date() == date(2026, 11, 1)


def test_web_saved_outlook_generation_csrf_profiles_and_public_projection(tmp_path, package, monkeypatch):
    monkeypatch.setenv("INVESTMENT_RESEARCH_ROOT", str(tmp_path / "context"))
    class Source:
        def load(self, ticker, cutoff):
            assert ticker == "SPCX" and cutoff >= CUTOFF
            return package
    with TestClient(create_app(tmp_path, scenario_source=Source())) as client:
        assert client.get("/api/scenarios/SPCX").status_code == 428
        client.get("/onboarding")
        client.headers["x-csrf-token"] = client.app.state.csrf
        client.post("/api/onboarding", json={"action": "skip", "step": 0, "answers": {}})
        assert client.get("/api/scenarios/SPCX").json() is None
        assert "OUTLOOK" in client.get("/research/SPCX").text
        assert client.post("/api/scenarios/SPCX", json={"risk_tolerance": "low"}).status_code == 422
        response = client.post("/api/scenarios/SPCX", json={})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["horizons"]) == 2
        assert all(len(h["scenarios"]) == 3 for h in payload["horizons"])
        text = json.dumps(payload)
        for hidden in ("confidence", "internal_weight", "possible_mechanism", "source_classification"):
            assert hidden not in text
        page = client.get("/research/SPCX").text
        assert page.count("<h4>Base case</h4>") == 2
        assert page.count("<h4>Bull case</h4>") == 2
        assert page.count("<h4>Bear case</h4>") == 2
        before = {p: p.read_bytes() for p in (tmp_path / "context").rglob("*.json")}
        assert client.get("/api/scenarios/SPCX").json() == payload
        assert before == {p: p.read_bytes() for p in before}
        del client.headers["x-csrf-token"]
        assert client.post("/api/scenarios/SPCX", json={}).status_code == 403


def test_optional_refresh_uses_injected_public_providers_without_reasoner(tmp_path):
    from test_research_service import FakeMarketData, FakeNews, sample_article
    path = refresh_public_scenario_inputs("SPCX", tmp_path, market=FakeMarketData(),
        news=FakeNews((sample_article(),)))
    raw = json.loads(path.read_text())
    assert raw["scope"]["ticker"] == "SPCX"
    assert raw["ranked_information_items"][0]["evidence_depth"] == "headline only"
    assert "investor_profile" not in raw
    with patch("investment_assistant.services.scenario_reasoning.ScenarioReasoningService.generate",
               side_effect=AssertionError("refresh must not call any reasoner")):
        assert path.exists()


def test_market_momentum_and_scheduled_policy_event_never_become_company_facts(regime):
    raw = raw_evidence()
    raw["fundamental_and_valuation_context"] = {}
    raw["ranked_information_items"] = [event(scope="market", scheduled="2026-11-12T13:00:00Z")]
    package = ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw, regime=regime)
    momentum = package.current_market_data[0].model_copy(update={"scope": "market",
        "asset_relevance": "market_context", "asset": "US"})
    package = ScenarioEvidencePackage.model_validate({**package.model_dump(),
                                                     "current_market_data": [momentum]})
    result = generate(package)
    drivers = result.scenarios[0].drivers
    assert all(d.scope == "market" for d in drivers)
    assert any("broad-market price momentum" in d.statement for d in drivers)
    assert any("A market event is scheduled" in d.statement for d in drivers)
    assert not any("company-specific price momentum" in d.statement for d in drivers)
    assert any("No verified upcoming company" in g for g in result.evidence_state.missing_evidence)


def test_regime_claim_must_agree_with_underlying_snapshot(package):
    record = package.regime_evidence[0].model_copy(update={"classification": "fabricated_uptrend"})
    with pytest.raises(ValidationError, match="disagrees"):
        ScenarioEvidencePackage.model_validate({**package.model_dump(),
            "regime_evidence": (record, *package.regime_evidence[1:])})


def test_future_valuation_price_and_secrets_in_audit_metadata_rejected(regime, package):
    raw = raw_evidence()
    raw["fundamental_and_valuation_context"]["decision_grade_metrics"][0]["price_date"] = "2026-11-06"
    evidence = ScenarioEvidenceBuilder().build("SPCX", CUTOFF, raw_evidence=raw, regime=regime)
    assert not evidence.fundamentals
    with pytest.raises(ValidationError, match="Secret-like"):
        ScenarioEvidencePackage.model_validate({**package.model_dump(),
            "source_artifacts": ["api_key=sk-this_must_never_appear_in_an_artifact"]})
