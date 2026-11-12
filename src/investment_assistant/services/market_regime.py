"""Deterministic equity market context. Adapters provide facts, never classifications."""

from __future__ import annotations

from bisect import bisect_right
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from math import sqrt
from statistics import mean, median, stdev

from investment_assistant.config import RegimeConfig
from investment_assistant.domain.regime import (
    DailyObservation, MarketRegimeSnapshot, RegimeDimension, RegimeEvidence,
)
from investment_assistant.ports.regime import (
    BroadMarketDataProvider, MacroDataProvider, RatesDataProvider, VolatilityDataProvider,
)


class ObservationSeries:
    """Normalize once, reject conflicting dates, and enforce both time cutoffs on every slice."""

    def __init__(self, observations=()):
        by_date = {}
        for observation in observations:
            if observation.date in by_date and by_date[observation.date] != observation:
                raise ValueError("Conflicting observations for the same series/date")
            by_date[observation.date] = observation
        self.rows = tuple(by_date[d] for d in sorted(by_date))
        if len({r.series for r in self.rows}) > 1:
            raise ValueError("A history must contain a single series")
        self.dates = tuple(o.date for o in self.rows)

    def through(self, as_of: date, limit: int | None = None) -> tuple[DailyObservation, ...]:
        rows = self.rows[:bisect_right(self.dates, as_of)]
        rows = tuple(o for o in rows if o.available_on <= as_of)
        return rows[-limit:] if limit else rows


@dataclass(frozen=True)
class RegimeHistories:
    market: dict[str, ObservationSeries]
    volatility: ObservationSeries
    rates: ObservationSeries
    unavailable: tuple[str, ...] = ()


def load_histories(market, volatility, rates, start, end, config) -> RegimeHistories:
    missing = []

    def fetch(provider, method, symbol):
        try:
            return ObservationSeries(getattr(provider, method)(symbol, start, end))
        except Exception:
            # Do not serialize exception messages: transport errors can contain credentials.
            missing.append(f"{symbol}: unavailable or invalid provider data")
            return ObservationSeries()

    return RegimeHistories(
        {s: fetch(market, "market_history", s) for s in config.benchmarks},
        fetch(volatility, "volatility_history", config.volatility_symbol),
        fetch(rates, "rates_history", config.rates_symbol), tuple(missing),
    )


def feature_evidence(rows, as_of, name, kind, config, series="unavailable") -> RegimeEvidence:
    """One transparent feature, retaining the exact observations used."""
    window = ()
    value = None
    unit = "percent"
    calculation = kind
    if kind.startswith("return_"):
        n = int(kind.split("_")[-1])
        window = rows[-(n + 1):]
        if len(window) == n + 1:
            value = (window[-1].value / window[0].value - 1) * 100
        calculation = f"100 * (close[T] / close[T-{n} sessions] - 1)"
    elif kind.startswith("ma_"):
        n = int(kind.split("_")[-1])
        window = rows[-n:]
        if len(window) == n:
            value = (window[-1].value / mean(r.value for r in window) - 1) * 100
        calculation = f"100 * (close[T] / mean(last {n} closes) - 1)"
    elif kind == "realised_volatility":
        window = rows[-21:]
        if len(window) == 21:
            changes = [b.value / a.value - 1 for a, b in zip(window, window[1:])]
            value = stdev(changes) * sqrt(252) * 100
        calculation = "sample stdev(last 20 daily simple returns) * sqrt(252) * 100"
    elif kind == "percentile":
        window = rows[-config.percentile_window:]
        if len(window) >= config.percentile_minimum:
            value = 100 * sum(r.value <= window[-1].value for r in window) / len(window)
        calculation = (
            f"100 * count(level <= latest) / count(last {config.percentile_window} levels); "
            f"minimum {config.percentile_minimum}; inclusive ties"
        )
    elif kind.startswith("change_"):
        n = int(kind.split("_")[-1])
        window = rows[-(n + 1):]
        if len(window) == n + 1:
            value = window[-1].value - window[0].value
        unit = "percentage points"
        calculation = f"yield[T] - yield[T-{n} sessions]"
    elif kind == "volume_ratio":
        window = rows[-20:]
        if len(window) == 20 and all(r.volume is not None for r in window):
            average = mean(r.volume for r in window)
            value = window[-1].volume / average if average > 0 else None
        unit = "ratio"
        calculation = "volume[T] / mean(last 20 volumes, including T)"
    elif kind == "level":
        window = rows[-1:]
        value = window[-1].value if window else None
        unit = window[-1].unit if window else "provider units"
        calculation = "latest available daily observation"
    else:
        raise ValueError("Unsupported feature calculation")
    latest = window[-1] if window else None
    stale = latest is not None and (as_of - latest.date).days > config.stale_after_days
    # Large internal gaps are incomplete evidence, not silently adjacent trading sessions.
    gaps = any((b.date - a.date).days > config.maximum_gap_days
               for a, b in zip(window, window[1:]))
    status = "missing" if value is None else "stale" if stale else "partial" if gaps else "available"
    return RegimeEvidence(
        reference=name, source=latest.source if latest else "not supplied",
        series=latest.series if latest else series, timestamp=latest.date if latest else None,
        retrieved_at=latest.retrieved_at if latest else None,
        observed_value=latest.value if latest else None, value=value, unit=unit,
        calculation=calculation, rule="feature input; dimension rule stored separately",
        confidence="unknown" if value is None else "low" if stale or gaps else "medium",
        status=status, observations=tuple(window),
    )


def unknown_dimension(name, reason="Reliable decision-grade evidence and rules are unavailable"):
    return RegimeDimension(
        classification="unknown", confidence="unknown", rule=reason,
        evidence=(RegimeEvidence(
            reference=name, source="not supplied", series=name, unit="unavailable",
            calculation="not calculated", rule=reason, status="missing",
        ),),
    )


def _confidence(votes, evidence):
    if not votes:
        return "unknown"
    if len(votes) == 1 or len(set(votes)) > 1:
        return "low"
    if any(e.status in {"stale", "partial"} for e in evidence if e.value is not None):
        return "low"
    return "high" if len(votes) >= 3 else "medium"


def _dimension(label, votes, evidence, rule, scores=None):
    confidence = _confidence(votes, evidence)
    if not votes:
        label = "unknown"
    elif (len({e.series for e in evidence if e.value is not None}) < 2
          or any(e.status == "missing" for e in evidence)):
        confidence = "low"
    return RegimeDimension(
        classification=label, confidence=confidence, rule=rule,
        evidence=tuple(e.model_copy(update={"rule": rule}) for e in evidence),
        component_scores=scores or {},
    )


def _sign(value, threshold):
    return 1 if value > threshold else -1 if value < -threshold else 0


def classify_regime(histories: RegimeHistories, as_of: date, config: RegimeConfig):
    trend_evidence, trend_votes, benchmark_votes = [], [], {}
    for symbol in config.benchmarks:
        rows = histories.market.get(symbol, ObservationSeries()).through(as_of, 61)
        indicators = [feature_evidence(rows, as_of, f"{symbol}:{kind}", kind, config, symbol)
                      for kind in ("return_5", "return_20", "return_60", "ma_20", "ma_60")]
        trend_evidence.extend(indicators)
        votes = [_sign(e.value, config.trend_ma_threshold if "ma_" in e.reference
                       else config.trend_return_threshold)
                 for e in indicators if e.value is not None]
        trend_votes.extend(votes)
        if votes:
            benchmark_votes[symbol] = mean(votes)
    trend_score = mean(trend_votes) if trend_votes else 0
    long_returns = [e.value for e in trend_evidence if e.reference.endswith("return_60")
                    and e.value is not None]
    trend = ("uptrend" if trend_score > config.trend_vote_threshold else
             "downtrend" if trend_score < -config.trend_vote_threshold else "sideways")
    if (trend_votes and all(v > 0 for v in trend_votes) and long_returns
            and min(long_returns) >= config.strong_trend_60d):
        trend = "strong_uptrend"
    if (trend_votes and all(v < 0 for v in trend_votes) and long_returns
            and max(long_returns) <= -config.strong_trend_60d):
        trend = "strong_downtrend"
    equity = _dimension(trend, trend_votes, trend_evidence,
                        "Mean signed return/MA votes crossing +/- trend_vote_threshold; "
                        "otherwise sideways. "
                        "Strong requires all votes agree and every available 60d return "
                        "crosses strong_trend_60d. Thresholds in configuration.", benchmark_votes)

    vix = histories.volatility.through(as_of, config.percentile_window)
    primary = histories.market.get(config.benchmarks[0], ObservationSeries()).through(as_of, 61)
    vol_evidence = [
        feature_evidence(vix, as_of, "volatility_level", "level", config, config.volatility_symbol),
        feature_evidence(vix, as_of, "volatility_percentile", "percentile", config,
                         config.volatility_symbol),
        feature_evidence(primary, as_of, "market_realised_volatility", "realised_volatility",
                         config, config.benchmarks[0]),
    ]
    vol_votes = [sum(e.value >= b for b in boundaries)
                 for e, boundaries in zip(vol_evidence, (
                     config.vix_boundaries, config.volatility_percentile_boundaries,
                     config.realised_vol_boundaries)) if e.value is not None]
    # Upper median: make disagreement visible, without pretending a single proxy is truth.
    vol_index = sorted(vol_votes)[len(vol_votes) // 2] if vol_votes else 0
    volatility = _dimension(("low", "normal", "elevated", "stressed")[vol_index],
                            vol_votes, vol_evidence,
                            "Upper median of ordinal VIX level, trailing percentile and "
                            "20-session realised-volatility bands; disagreement lowers confidence.")
    rate_rows = histories.rates.through(as_of, 61)
    rate_evidence = [feature_evidence(rate_rows, as_of, f"rates:{kind}", kind, config,
                                     config.rates_symbol)
                     for kind in ("level", "change_5", "change_20")]
    rate_votes = [_sign(e.value, threshold) for e, threshold in zip(
        rate_evidence[1:], (config.rates_short_change, config.rates_medium_change))
        if e.value is not None]
    rate_score = median(rate_votes) if rate_votes else 0
    rates = _dimension("rising" if rate_score > 0 else "falling" if rate_score < 0 else "stable",
                       rate_votes, rate_evidence,
                       "Median signed 5/20-session yield changes in percentage points. "
                       "Market yield direction does not imply Federal Reserve policy.")

    risk_components = dict(benchmark_votes)
    if vol_votes:
        risk_components["volatility"] = config.volatility_risk_scores[vol_index]
    risk_score = mean(risk_components.values()) if risk_components else None
    risk_label = ("unknown" if risk_score is None else
                  "risk_on" if risk_score > config.risk_score_threshold else
                  "risk_off" if risk_score < -config.risk_score_threshold else "neutral")
    risk = _dimension(risk_label, [_sign(v, 0.01) for v in risk_components.values()],
                      trend_evidence + vol_evidence,
                      "Equal-weight mean of available benchmark trend scores and volatility "
                      "score; compare with +/- risk_score_threshold. No credit/sector proxy supplied.",
                      {**risk_components, **({"total": risk_score} if risk_score is not None else {})})
    return MarketRegimeSnapshot(
        as_of=as_of, benchmark=config.benchmarks[0], equity_trend=equity,
        volatility=volatility, rates=rates, risk_sentiment=risk,
        monetary_policy=unknown_dimension("monetary_policy"),
        inflation=unknown_dimension("inflation"), growth=unknown_dimension("growth"),
        configuration={**json.loads(json.dumps(asdict(config))),
                       "provider_status": list(histories.unavailable)},
    )


class MarketRegimeService:
    def __init__(self, market: BroadMarketDataProvider, volatility: VolatilityDataProvider,
                 rates: RatesDataProvider, macro: MacroDataProvider | None = None,
                 config: RegimeConfig | None = None):
        self.market, self.volatility, self.rates, self.macro = market, volatility, rates, macro
        self.config = config or RegimeConfig()

    def histories(self, start, end):
        return load_histories(self.market, self.volatility, self.rates, start, end, self.config)

    def snapshot(self, as_of: date) -> MarketRegimeSnapshot:
        result = classify_regime(self.histories(as_of - timedelta(days=550), as_of),
                                 as_of, self.config)
        if self.macro is None:
            return result
        # Architecture for official release vintages. No unreviewed macro rules in v1.
        updates = {}
        for name in ("monetary_policy", "inflation", "growth"):
            try:
                rows = tuple(o for o in self.macro.macro_history(name, as_of)
                             if o.published_on <= as_of and o.observation_date <= as_of
                             and o.decision_grade)
            except Exception:
                rows = ()
            reason = ("Official vintages supplied; classification rule not enabled in v1"
                      if rows else "No decision-grade publication-dated macro evidence available")
            updates[name] = unknown_dimension(name, reason)
            if rows:
                updates[name] = RegimeDimension(
                    classification="unknown", confidence="unknown", rule=reason,
                    evidence=tuple(RegimeEvidence(
                        reference=f"{name}:{o.series}:{o.vintage}", source=o.source,
                        series=o.series, timestamp=o.published_on, retrieved_at=o.retrieved_at,
                        observed_value=o.value, value=o.value, unit=o.unit,
                        calculation="official publication-dated vintage; no v1 classification",
                        rule=reason, status="available", macro_observations=(o,),
                    ) for o in rows),
                )
        return MarketRegimeSnapshot.model_validate({**result.model_dump(), **updates})
