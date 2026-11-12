"""All evidence is synthetic test data; no live provider or paid API is used."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from math import sin

import pytest

from investment_assistant.config import RegimeConfig
from investment_assistant.domain.regime import DailyObservation, MarketRegimeSnapshot
from investment_assistant.services.market_regime import (
    MarketRegimeService, ObservationSeries, feature_evidence,
)


def trading_dates(size=360):
    dates, day = [], date(2023, 1, 2)
    while len(dates) < size:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)
    return dates


def observations(symbol, values, dates=None, volumes=True):
    days = dates or trading_dates(len(values))
    return tuple(DailyObservation(
        date=day, available_on=day, value=float(value),
        volume=1000 + i % 7 * 20 if volumes else None,
        source="synthetic offline fixture", series=symbol,
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        unit="yield percent" if symbol == "^TNX" else "index points" if symbol == "^VIX"
        else "price",
    ) for i, (day, value) in enumerate(zip(days, values)))


class FakeHistory:
    def __init__(self, size=360, direction=1, vix=18, rate_direction=1, target_size=None):
        days = trading_dates(size)
        self.data = {
            s: observations(s, [100 * (1 + direction * 0.003) ** i for i in range(size)], days)
            for s in ("SPY", "QQQ")
        }
        self.data["^VIX"] = observations("^VIX", [vix + sin(i) * 0.5 for i in range(size)], days)
        self.data["^TNX"] = observations("^TNX", [4 + rate_direction * i * 0.005
                                                   for i in range(size)], days)
        self.data["TEST"] = observations("TEST", [100 * (1 + 0.0003 * i)
                                                  * (1 + 0.08 * sin(i / 9))
                                                  for i in range(size)], days)
        if target_size is not None:
            self.data["TEST"] = self.data["TEST"][-target_size:] if target_size else ()

    # Deliberately return future data too: services must enforce cutoffs independently.
    def market_history(self, symbol, start, end):
        return self.data.get(symbol, ())

    volatility_history = market_history
    rates_history = market_history

    def service(self, config=None):
        return MarketRegimeService(self, self, self, config=config)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("External network/paid API forbidden in context tests")
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("yfinance.Ticker.history", forbidden)
    monkeypatch.setattr("openai.resources.responses.Responses.create", forbidden)


@pytest.mark.parametrize("direction,label", [(1, "strong_uptrend"), (-1, "strong_downtrend")])
def test_clear_equity_trend(direction, label):
    data = FakeHistory(direction=direction)
    result = data.service().snapshot(trading_dates()[-1])
    assert result.equity_trend.classification == label
    assert result.equity_trend.confidence == "high"
    assert all(e.source and e.calculation and e.rule and e.observations
               for e in result.equity_trend.evidence)
    assert MarketRegimeSnapshot.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("vix,expected", [(12, "low"), (18, "normal"),
                                          (30, "elevated"), (45, "stressed")])
def test_volatility_bands(vix, expected):
    data = FakeHistory(vix=vix)
    # Level and percentile agree at final observation, realised volatility disagrees.
    levels = list(data.data["^VIX"])
    for i, row in enumerate(levels):
        levels[i] = row.model_copy(update={"value": float(vix + (i % 10) - 5)})
    percentile_target = {12: 10, 18: 50, 30: 85, 45: 99}[vix]
    final = float(vix)
    cutoff = round(251 * percentile_target / 100)
    for i in range(len(levels) - 252, len(levels) - 1):
        levels[i] = levels[i].model_copy(update={"value": final - 1 if
                                               i - (len(levels) - 252) < cutoff else final + 1})
    levels[-1] = levels[-1].model_copy(update={"value": final})
    data.data["^VIX"] = tuple(levels)
    result = data.service().snapshot(trading_dates()[-1])
    assert result.volatility.classification == expected
    if expected != "low":
        assert result.volatility.confidence == "low"


@pytest.mark.parametrize("direction,label", [(1, "rising"), (-1, "falling"), (0, "stable")])
def test_yield_direction(direction, label):
    data = FakeHistory(rate_direction=direction)
    result = data.service().snapshot(trading_dates()[-1])
    # Use configurable thresholds appropriate for the fixture's 5/20-day changes.
    result = data.service(replace(RegimeConfig(), rates_short_change=.01,
                                  rates_medium_change=.05)).snapshot(trading_dates()[-1])
    assert result.rates.classification == label
    assert "does not imply" in result.rates.rule


def test_conflict_reduces_trend_confidence():
    data = FakeHistory()
    down = FakeHistory(direction=-1)
    data.data["QQQ"] = down.data["QQQ"]
    result = data.service().snapshot(trading_dates()[-1])
    assert result.equity_trend.classification == "sideways"
    assert result.equity_trend.confidence == "low"


def test_missing_macro_unknown_and_missing_market_explicit():
    data = FakeHistory()
    data.data = {}
    result = data.service().snapshot(trading_dates()[-1])
    assert set(result.classification.values()) == {"unknown"}
    assert all(getattr(result, key).confidence == "unknown" for key in result.classification)
    assert result.rates.evidence[0].value is None


def test_stale_data_lowers_confidence_and_records_status():
    data = FakeHistory()
    result = data.service().snapshot(trading_dates()[-1] + timedelta(days=15))
    assert result.equity_trend.confidence == "low"
    assert all(e.status == "stale" for e in result.equity_trend.evidence)


def test_asof_has_no_future_inputs_even_when_provider_ignores_end():
    data = FakeHistory()
    as_of = trading_dates()[150]
    before = data.service().snapshot(as_of)
    for symbol, rows in data.data.items():
        data.data[symbol] = tuple(r.model_copy(update={"value": r.value * 100})
                                  if r.date > as_of else r for r in rows)
    assert data.service().snapshot(as_of) == before
    assert all(o.date <= as_of for e in before.equity_trend.evidence for o in e.observations)


def test_delayed_availability_and_duplicate_protection():
    rows = observations("SPY", [100, 200])
    delayed = rows[-1].model_copy(update={"available_on": rows[-1].date + timedelta(days=10)})
    series = ObservationSeries((rows[0], delayed))
    assert series.through(rows[-1].date) == (rows[0],)
    with pytest.raises(ValueError, match="Conflicting"):
        ObservationSeries((rows[0], rows[0].model_copy(update={"value": 2.0})))


def test_missing_feature_not_zero_and_internal_gap_partial():
    config = RegimeConfig()
    rows = observations("SPY", [100, 100])
    e = feature_evidence(rows, rows[-1].date, "r", "return_60", config)
    assert e.value is None and e.status == "missing"
    rows = tuple(r.model_copy(update={"date": r.date + timedelta(days=30),
                                      "available_on": r.date + timedelta(days=30)})
                 if i > 10 else r for i, r in enumerate(observations("SPY", [100] * 21)))
    e = feature_evidence(rows, rows[-1].date, "r", "realised_volatility", config)
    assert e.status == "partial"


def test_provider_error_never_serializes_secrets():
    class Broken(FakeHistory):
        def market_history(self, *args):
            raise RuntimeError("https://example.test?api_key=SECRET")
    data = Broken()
    result = data.service().snapshot(trading_dates()[-1])
    assert result.equity_trend.classification == "unknown"
    assert "SECRET" not in result.model_dump_json()


def test_official_macro_port_does_not_guess_without_enabled_rules():
    class Macro:
        def macro_history(self, dimension, as_of):
            return ()
    data = FakeHistory()
    service = MarketRegimeService(data, data, data, Macro())
    result = service.snapshot(trading_dates()[-1])
    assert result.monetary_policy.classification == "unknown"
    assert result.inflation.classification == "unknown"
    assert result.growth.classification == "unknown"


@pytest.mark.parametrize("direction,label", [(1, "uptrend"), (-1, "downtrend")])
def test_moderate_trend_not_strong(direction, label):
    data = FakeHistory()
    for symbol in ("SPY", "QQQ"):
        data.data[symbol] = observations(symbol, [100 * (1 + direction * .0012) ** i
                                                 for i in range(360)])
    assert data.service().snapshot(trading_dates()[-1]).equity_trend.classification == label


def test_single_market_proxy_lowers_confidence():
    data = FakeHistory()
    snapshot = data.service(replace(RegimeConfig(), benchmarks=("SPY",))).snapshot(
        trading_dates()[-1])
    assert snapshot.equity_trend.confidence == "low"


def test_macro_vintage_evidence_retained_and_future_publications_rejected():
    from investment_assistant.domain.regime import MacroObservation

    day = trading_dates()[-1]
    observation = MacroObservation(series="official:test", source="synthetic official fixture",
        observation_date=day - timedelta(days=30), published_on=day,
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), value=3.0, unit="percent",
        vintage="fixture-v1", decision_grade=True)
    class Macro:
        def macro_history(self, dimension, as_of):
            return (observation, observation.model_copy(update={
                "published_on": day + timedelta(days=1), "value": 9.0}))
    data = FakeHistory()
    result = MarketRegimeService(data, data, data, Macro()).snapshot(day)
    assert result.inflation.classification == "unknown"
    assert len(result.inflation.evidence) == 1
    assert result.inflation.evidence[0].macro_observations == (observation,)
