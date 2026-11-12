"""Offline controls for similarity, temporal separation, missingness and outcomes."""

from dataclasses import replace
from datetime import timedelta

import pytest

from investment_assistant.config import AnalogueConfig
from investment_assistant.services.historical_analogues import (
    HistoricalAnalogueService, forward_outcomes, sample_warning, similarity,
)
from investment_assistant.services.historical_features import HistoricalFeatureBuilder
from investment_assistant.services.market_regime import ObservationSeries
from test_market_regime import FakeHistory, no_network, observations, trading_dates  # noqa: F401


def builder(data):
    service = data.service()
    dates = trading_dates()
    return HistoricalFeatureBuilder(service.histories(dates[0], dates[-1]),
                                    ObservationSeries(data.data["TEST"]), "TEST")


def test_identical_vectors_maximum_and_dissimilar_categories_lower():
    current = builder(FakeHistory()).build(trading_dates()[-1])
    weights = {"broad_market_20d_return": 1}
    scales = {"broad_market_20d_return": 10}
    assert similarity(current, current, weights, scales)[0] == 100
    other = builder(FakeHistory(direction=-1)).build(trading_dates()[-1])
    assert similarity(current, other, weights, scales)[0] < 50
    altered = current.regime.model_copy(update={"equity_trend":
        current.regime.equity_trend.model_copy(update={"classification": "downtrend"})})
    other = current.model_copy(update={"regime": altered})
    assert similarity(current, other, weights, scales)[0] == 97.5


def test_weights_missing_penalty_and_no_imputation():
    current = builder(FakeHistory()).build(trading_dates()[-1])
    a = current.model_copy(update={"features": {"x": 0.0, "y": 0.0}})
    b = current.model_copy(update={"features": {"x": 10.0, "y": 0.0}})
    assert similarity(a, b, {"x": .9, "y": .1}, {"x": 10, "y": 10})[0] == pytest.approx(10)
    assert similarity(a, b, {"x": .1, "y": .9}, {"x": 10, "y": 10})[0] == 90
    missing = a.model_copy(update={"features": {"x": None, "y": 0.0}})
    score, _, _, coverage = similarity(a, missing, {"x": .9, "y": .1}, {"x": 10, "y": 10})
    assert score == pytest.approx(10) and coverage == .1


def test_top_n_ranking_repeatable_and_outcome_embargo():
    data = FakeHistory()
    service = HistoricalAnalogueService(data.service())
    as_of = trading_dates()[-1]
    result = service.search("TEST", as_of, top=5)
    assert result.mode == "asset_specific"
    assert len(result.matches) == 5
    assert [m.similarity for m in result.matches] == sorted(
        (m.similarity for m in result.matches), reverse=True)
    assert result == service.search("TEST", as_of, top=5)
    assert sum(result.weights.values()) == pytest.approx(1)
    feature_start = trading_dates()[-61]
    assert all(o.end < feature_start for m in result.matches for o in m.outcomes)
    assert all(o.date <= m.date for m in result.matches
               for e in m.snapshot.feature_evidence.values() for o in e.observations)
    assert all(abs(trading_dates().index(a.date) - trading_dates().index(b.date)) >= 20
               for i, a in enumerate(result.matches) for b in result.matches[i + 1:])


def test_future_changes_cannot_change_historical_features_or_search_asof():
    data = FakeHistory()
    cutoff = trading_dates()[280]
    original = HistoricalAnalogueService(data.service()).search("TEST", cutoff, 3)
    for symbol, rows in data.data.items():
        data.data[symbol] = tuple(r.model_copy(update={"value": r.value * 25,
                                                       "volume": 999999})
                                  if r.date > cutoff else r for r in rows)
    assert HistoricalAnalogueService(data.service()).search("TEST", cutoff, 3) == original
    assert all(o.end <= cutoff for m in original.matches for o in m.outcomes)


def test_feature_builder_ignores_outcome_values():
    data = FakeHistory()
    day = trading_dates()[160]
    old = builder(data).build(day)
    for symbol, rows in data.data.items():
        data.data[symbol] = tuple(r.model_copy(update={"value": 10000.0})
                                  if r.date > day else r for r in rows)
    assert builder(data).build(day) == old


def test_forward_outcomes_excess_drawdown_negative_and_unmatured():
    days = trading_dates(6)
    target = ObservationSeries(observations("TEST", [100, 90, 80, 85, 90, 95], days))
    benchmark = ObservationSeries(observations("SPY", [100, 101, 102, 103, 104, 105], days))
    one, five, missing = forward_outcomes(target, benchmark, days[0], days[-1], (1, 5, 20))
    assert one.raw_return == pytest.approx(-10)
    assert five.raw_return == pytest.approx(-5)
    assert five.benchmark_return == pytest.approx(5)
    assert five.excess_return == pytest.approx(-10)
    assert five.maximum_drawdown == pytest.approx(-20)
    assert five.start == days[0] and five.end == days[-1]
    assert missing.raw_return is None and missing.end is None
    assert all(r.date > days[0] for r in five.evidence[0].observations[1:])
    limited = forward_outcomes(target, benchmark, days[0], days[2], (5,))[0]
    assert limited.raw_return is None and limited.benchmark_return is None


def test_exact_sessions_required_no_invented_target_outcome():
    days = trading_dates(6)
    broad = ObservationSeries(observations("SPY", [100] * 6, days))
    partial = ObservationSeries(observations("TEST", [100] * 5, days[1:]))
    result = forward_outcomes(partial, broad, days[0], days[-1], (5,))[0]
    assert result.raw_return is None and result.excess_return is None
    assert result.benchmark_return == 0


def test_new_listing_market_only_no_company_specific_outcomes():
    data = FakeHistory(target_size=15)
    result = HistoricalAnalogueService(data.service()).search("TEST", trading_dates()[-1], 3)
    assert result.mode == "market_regime_only" and result.sample_size == 3
    assert not any(key.startswith("target_asset_") for key in result.weights)
    assert all(o.raw_return is None and o.excess_return is None
               for m in result.matches for o in m.outcomes)
    assert all(o.benchmark_return is not None for m in result.matches for o in m.outcomes)
    assert all(s.sample_size == 0 and s.benchmark_sample_size == 3 for s in result.summaries)


def test_target_has_enough_rows_but_no_eligible_history_falls_back():
    data = FakeHistory(target_size=130)
    result = HistoricalAnalogueService(data.service()).search("TEST", trading_dates()[-1], 3)
    assert result.mode == "market_regime_only"


def test_negative_analogues_and_summary_preserved():
    data = FakeHistory()
    data.data["TEST"] = observations("TEST", [200 * .998 ** i for i in range(360)])
    result = HistoricalAnalogueService(data.service()).search("TEST", trading_dates()[-1], 3)
    assert all(o.raw_return < 0 for m in result.matches for o in m.outcomes)
    assert all(s.positive_return_frequency == 0 for s in result.summaries)
    assert all(s.mean_forward_return < 0 and s.worst_historical_outcome < 0
               and s.best_historical_outcome < 0 for s in result.summaries)
    row = result.matches[0].training_row()
    assert row["future_20d_return"] < 0
    assert "future_20d_return" not in result.current.features


def test_missing_sources_safe_feature_exclusion_and_no_fabricated_match():
    data = FakeHistory()
    data.data.pop("^TNX")
    result = HistoricalAnalogueService(data.service()).search("TEST", trading_dates()[-1], 2)
    assert "10y_yield_level" in result.excluded_features
    assert "10y_yield_level" not in result.weights
    data.data = {}
    result = HistoricalAnalogueService(data.service()).search("TEST", trading_dates()[-1], 2)
    assert result.sample_size == 0 and result.weights == {}
    assert result.sample_warning == "insufficient historical sample"


@pytest.mark.parametrize("size,warning", [(0, "insufficient historical sample"),
    (4, "insufficient historical sample"), (5, "very limited evidence"),
    (9, "very limited evidence"), (10, "limited evidence"), (29, "limited evidence"),
    (30, "more informative but still not predictive")])
def test_sample_warnings(size, warning):
    assert sample_warning(size) == warning


def test_invalid_weights_and_unsupported_asset():
    with pytest.raises(ValueError):
        AnalogueConfig(weights={"x": -1}, scales={"x": 1})
    with pytest.raises(ValueError):
        AnalogueConfig(weights={"x": 1}, scales={"x": 0})
    with pytest.raises(ValueError):
        replace(AnalogueConfig(), outcome_windows=(0,))
    with pytest.raises(ValueError, match="equities"):
        HistoricalAnalogueService(FakeHistory().service()).search("BRNT.L", trading_dates()[-1])


def test_zero_recency_preference_with_tied_scores():
    data = FakeHistory()
    config = AnalogueConfig(weights={"volatility_level": 1}, scales={"volatility_level": 20},
                            category_penalty=0, separation_sessions=0)
    data.data["^VIX"] = observations("^VIX", [18] * 360)
    result = HistoricalAnalogueService(data.service(), config=config).search(
        "TEST", trading_dates()[-1], 3)
    assert [m.date for m in result.matches] == trading_dates()[60:63]


def test_stale_features_remain_null():
    data = FakeHistory()
    future = trading_dates()[-1] + timedelta(days=15)
    snapshot = builder(data).build(future)
    assert all(v is None for v in snapshot.features.values())
