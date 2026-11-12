"""Case explanations use recorded facts only and leave scores and ranking untouched."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from investment_assistant.config import AnalogueConfig
from investment_assistant.services.analogue_cases import (
    MATCHING_DISTANCE_MAXIMUM, build_case_card, build_case_cards,
)
from investment_assistant.services.context_artifacts import ContextArtifactStore
from investment_assistant.services.context_cli import print_analogues
from investment_assistant.services.historical_analogues import HistoricalAnalogueService
from investment_assistant.web.app import create_app
from test_market_regime import FakeHistory, no_network, trading_dates  # noqa: F401


@pytest.fixture(scope="module")
def result():
    # Presentation tests need ten saved matches, independent of default date de-clustering.
    return HistoricalAnalogueService(FakeHistory().service(), config=AnalogueConfig(
        separation_sessions=0)).search(
        "TEST", trading_dates()[-1], 10)


def change_outcomes(match, five, twenty, drawdown=-8.0):
    return match.model_copy(update={"outcomes": tuple(o.model_copy(update={
        "benchmark_return": five if o.trading_days == 5 else twenty,
        "benchmark_maximum_drawdown": drawdown,
    }) for o in match.outcomes if o.trading_days in (5, 20))})


def test_cards_keep_every_date_score_and_order_without_recomputing_or_mutating(result):
    before = result.model_dump_json()
    with patch("investment_assistant.services.historical_analogues.similarity",
               side_effect=AssertionError("case cards must not score")):
        cards = build_case_cards(result)
    assert len(cards) == 10
    assert [(c.historical_date, c.similarity) for c in cards] == [
        (m.date, m.similarity) for m in result.matches]
    assert result.model_dump_json() == before
    assert build_case_cards(result) == cards
    for match, card in zip(result.matches, cards):
        assert sum(c.weighted_distance for c in card.feature_comparisons) == pytest.approx(
            match.feature_distance)
        assert len(card.historical_regime) == 7
        assert card.what_this_case_can_teach_us
        assert card.why_this_case_may_not_apply_today


def test_matching_and_mismatching_features_show_real_values_and_weighted_importance(result):
    match = result.matches[0]
    current = result.current.model_copy(update={"features": {"x": 0.0, "y": 0.0, "z": 0.0}})
    snapshot = match.snapshot.model_copy(update={"features": {"x": 9.0, "y": 5.0, "z": 0.1}})
    custom = result.model_copy(update={"current": current, "weights": {"x": .1, "y": .8,
                                                                              "z": .1},
                                       "scales": {"x": 10, "y": 10, "z": 10}})
    card = build_case_card(custom, match.model_copy(update={"snapshot": snapshot}))
    assert [f.feature for f in card.mismatching_features] == ["y", "x"]
    assert [f.feature for f in card.matching_features] == ["z"]
    assert card.mismatching_features[0].historical == 5
    assert card.mismatching_features[0].current == 0
    assert "weight 80.0%" in card.mismatching_features[0].explanation
    assert all(f.normalized_distance <= MATCHING_DISTANCE_MAXIMUM for f in card.matching_features)


def test_missing_is_never_zero_or_an_identical_feature_match(result):
    match = result.matches[0]
    custom = result.model_copy(update={
        "current": result.current.model_copy(update={"features": {"risk_sentiment_score": None}}),
        "weights": {"risk_sentiment_score": 1.0}, "scales": {"risk_sentiment_score": 2.0}})
    snapshot = match.snapshot.model_copy(update={"features": {"risk_sentiment_score": None}})
    card = build_case_card(custom, match.model_copy(update={"snapshot": snapshot}))
    comparison = card.feature_comparisons[0]
    assert comparison.current is None and comparison.historical is None
    assert comparison.status == "unavailable" and comparison.normalized_distance == 1
    assert not card.matching_features and not card.mismatching_features
    assert any("Missing comparison" in text for text in card.unavailable_evidence)
    assert not any("inflation" in s.lower() for s in card.similarities)
    assert any("Inflation regime unavailable for both dates" in s
               for s in card.unavailable_evidence)


def test_regime_differences_include_both_dates_and_confidence(result):
    match = result.matches[0]
    historical = match.snapshot.regime.model_copy(update={"equity_trend":
        match.snapshot.regime.equity_trend.model_copy(update={"classification": "downtrend"})})
    snapshot = match.snapshot.model_copy(update={"regime": historical})
    card = build_case_card(result, match.model_copy(update={"snapshot": snapshot}))
    assert any("historical downtrend versus current strong uptrend" in s for s in card.differences)
    assert card.historical_regime[0].current == "strong uptrend"
    assert card.historical_regime[0].historical_confidence == historical.equity_trend.confidence


@pytest.mark.parametrize("five,twenty,phrase", [
    (-3.07, -6.48, "near-term weakness"), (3.0, 6.0, "near-term gains"),
    (-3.0, 6.0, "differed in direction"), (0.0, 0.0, "unchanged"),
    (0.0, 6.0, "flat return"),
])
def test_lessons_reflect_actual_signs_without_predicting(result, five, twenty, phrase):
    match = change_outcomes(result.matches[0], five, twenty)
    card = build_case_card(result, match)
    assert phrase in card.what_this_case_can_teach_us
    assert "not a forecast or a causal explanation" in card.what_this_case_can_teach_us
    assert card.outcomes[0].benchmark_return == five
    assert card.outcomes[1].benchmark_return == twenty
    assert all(o.benchmark_drawdown == -8 for o in card.outcomes)
    if five > 0 or twenty > 0:
        assert "positive endpoint return" in card.what_this_case_can_teach_us


def test_missing_outcome_and_drawdown_stay_unavailable(result):
    match = result.matches[0].model_copy(update={"outcomes": ()})
    card = build_case_card(result, match)
    assert all(o.benchmark_return is None and o.benchmark_drawdown is None for o in card.outcomes)
    assert "outcomes are unavailable" in card.what_this_case_can_teach_us
    single = result.matches[0].model_copy(update={
        "outcomes": tuple(o for o in result.matches[0].outcomes if o.trading_days == 5)})
    assert "Only one requested" in build_case_card(result, single).what_this_case_can_teach_us


def test_market_only_limits_and_exclusions_do_not_invent_stock_outcomes():
    result = HistoricalAnalogueService(FakeHistory(target_size=10).service()).search(
        "TEST", trading_dates()[-1], 2)
    card = build_case_cards(result)[0]
    assert "not a TEST company-specific analogue" in card.why_this_case_may_not_apply_today[0]
    assert all(o.asset_return is None and o.excess_return is None for o in card.outcomes)
    assert any("Target-asset 20-day momentum unavailable" in s for s in card.unavailable_evidence)
    assert any("n=2; insufficient historical sample" in s
               for s in card.why_this_case_may_not_apply_today)


def test_cli_cards_use_benchmark_name_and_all_requested_sections(result, capsys):
    custom = result.model_copy(update={"benchmark": "QQQ", "matches": result.matches[:1]})
    print_analogues(custom)
    output = capsys.readouterr().out
    for heading in ("Historical analogues for", "similarity", "20-day benchmark:",
                    "60-day benchmark:", "not forecasts"):
        assert heading in output
    for hidden in ("confidence", "Differences:", "5-day", "1-day"):
        assert hidden not in output



def test_saved_legacy_results_gain_cards_without_rewriting_or_expanding_ai_evidence(result,
                                                                                 tmp_path):
    store = ContextArtifactStore(tmp_path)
    path = store.save(result)
    original = path.read_bytes()
    web = store.read_context("TEST", result.as_of, include_cases=True)["historical_analogues"]
    assert len(web["matches"]) == 10
    assert all(m["historical_case"]["similarity_score"] == m["similarity"] for m in web["matches"])
    assert path.read_bytes() == original
    compact = store.read_context("TEST", result.as_of)["historical_analogues"]
    assert len(compact["matches"]) == 3 and all("historical_case" not in m for m in compact["matches"])
    assert not build_case_cards(result.model_copy(update={"matches": ()}))


def test_web_all_cards_expand_and_escape_feature_labels(result, tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTMENT_RESEARCH_ROOT", str(tmp_path / "context"))
    store = ContextArtifactStore(tmp_path / "context")
    hostile = "<script>alert(1)</script>"
    first = result.matches[0]
    altered = first.snapshot.model_copy(update={"features": {hostile: 0.0}})
    current = result.current.model_copy(update={"features": {hostile: 0.0}})
    result = result.model_copy(update={"current": current,
        "matches": (first.model_copy(update={"snapshot": altered}), *result.matches[1:]),
        "weights": {hostile: 1.0}, "scales": {hostile: 1.0}})
    store.save(result)
    with TestClient(create_app(tmp_path)) as client:
        client.get("/onboarding")
        client.headers["x-csrf-token"] = client.app.state.csrf
        client.post("/api/onboarding", json={"action": "skip", "step": 0, "answers": {}})
        response = client.get("/research/TEST")
        assert response.status_code == 200
        assert response.text.count('<details class="panel analogue-case">') == 10
        assert response.text.count("WHAT IS SIMILAR TO TODAY?") == 10
        assert "What can we learn?" not in response.text
        assert "Why this case may not apply today" not in response.text
        assert "Benchmark maximum drawdown" in response.text
        assert hostile not in response.text
        cards = client.get("/api/market-context/TEST").json()["historical_analogues"]["matches"]
        assert len(cards) == 10 and cards[0]["historical_case"]["analogue_date"] == str(first.date)


