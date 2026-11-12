"""Weighted deterministic historical comparisons with separately calculated forward outcomes."""

from dataclasses import asdict
import json
from datetime import timedelta
from math import isfinite
from statistics import mean, median

from investment_assistant.config import AnalogueConfig
from investment_assistant.domain.analogues import (
    AnalogueSearchResult, HistoricalAnalogue, HistoricalForwardOutcome, OutcomeSummary,
)
from investment_assistant.domain.assets import AssetType, asset_profile_for
from investment_assistant.domain.regime import RegimeEvidence
from investment_assistant.services.historical_features import HistoricalFeatureBuilder
from investment_assistant.services.market_regime import ObservationSeries
from investment_assistant.services.news_service import TICKER_PATTERN


def sample_warning(size):
    if size < 5:
        return "insufficient historical sample"
    if size < 10:
        return "very limited evidence"
    if size < 30:
        return "limited evidence"
    return "more informative but still not predictive"


def similarity(current, candidate, weights, scales, category_penalty=0.10):
    """Fixed scales; missing comparisons incur maximum per-feature distance."""
    if not weights or sum(weights.values()) <= 0:
        return 0.0, 1.0, 0.0, 0.0
    if any(not isfinite(w) or w < 0 or k not in scales or not isfinite(scales[k])
           or scales[k] <= 0 for k, w in weights.items()):
        raise ValueError("Invalid similarity weights or scales")
    total, distance, present = sum(weights.values()), 0.0, 0.0
    for key, weight in weights.items():
        a, b = current.features.get(key), candidate.features.get(key)
        if a is None or b is None:
            distance += weight
        else:
            distance += weight * min(abs(a - b) / scales[key], 1.0)
            present += weight
    distance /= total
    # Unknown categories are excluded explicitly, never interpreted as a match.
    categories = [(current.regime.classification[k], candidate.regime.classification[k])
                  for k in ("equity_trend", "volatility", "rates", "risk_sentiment")]
    comparable = [(a, b) for a, b in categories if a != "unknown" and b != "unknown"]
    penalty = category_penalty * sum(a != b for a, b in comparable) / max(len(comparable), 1)
    return (max(0.0, 100 * (1 - min(1.0, distance + penalty))), distance, penalty,
            min(1.0, present / total))


def _drawdown(rows):
    peak = rows[0].value
    worst = 0.0
    for row in rows:
        peak = max(peak, row.value)
        worst = min(worst, (row.value / peak - 1) * 100)
    return worst


def _outcome_evidence(rows, name):
    latest = rows[-1]
    return RegimeEvidence(
        reference=name, source=latest.source, series=latest.series, timestamp=latest.date,
        retrieved_at=latest.retrieved_at, observed_value=latest.value,
        value=(latest.value / rows[0].value - 1) * 100, unit="percent price return",
        calculation="100 * (end close / analogue-date close - 1)",
        rule="Outcome-only observations; never similarity inputs", confidence="medium",
        status="available", observations=tuple(rows),
    )


def forward_outcomes(target, benchmark, analogue_date, as_of, windows=(1, 5, 20, 60),
                     include_asset=True):
    """Use benchmark trading sessions and exact common endpoints. No date substitution."""
    broad_rows = benchmark.through(as_of)
    dates = [r.date for r in broad_rows]
    if analogue_date not in dates:
        return ()
    position = dates.index(analogue_date)
    target_rows = {r.date: r for r in target.through(as_of)} if include_asset else {}
    outcomes = []
    for window in windows:
        end_index = position + window
        broad = broad_rows[position:end_index + 1] if end_index < len(dates) else ()
        end = broad[-1].date if broad else None
        required_dates = dates[position:end_index + 1] if broad else []
        asset = tuple(target_rows[d] for d in required_dates if d in target_rows)
        asset_ok = bool(broad) and len(asset) == len(required_dates)
        benchmark_return = (broad[-1].value / broad[0].value - 1) * 100 if broad else None
        raw_return = (asset[-1].value / asset[0].value - 1) * 100 if asset_ok else None
        evidence = []
        if broad:
            evidence.append(_outcome_evidence(broad, "benchmark_forward_return"))
        if asset_ok:
            evidence.append(_outcome_evidence(asset, "asset_forward_return"))
        outcomes.append(HistoricalForwardOutcome(
            trading_days=window, start=analogue_date, end=end, raw_return=raw_return,
            benchmark_return=benchmark_return,
            excess_return=raw_return - benchmark_return if raw_return is not None else None,
            maximum_drawdown=_drawdown(asset) if asset_ok else None,
            benchmark_maximum_drawdown=_drawdown(broad) if broad else None,
            asset_status="available" if asset_ok else
                         "not applicable in market_regime_only mode" if not include_asset else
                         "missing exact endpoints or sessions",
            benchmark_status="available" if broad else "outcome window not yet available",
            evidence=tuple(evidence),
        ))
    return tuple(outcomes)


def summarize(matches, windows):
    summaries = []
    for window in windows:
        outcomes = [o for m in matches for o in m.outcomes if o.trading_days == window]
        raw = [o.raw_return for o in outcomes if o.raw_return is not None]
        excess = [o.excess_return for o in outcomes if o.excess_return is not None]
        broad = [o.benchmark_return for o in outcomes if o.benchmark_return is not None]
        summaries.append(OutcomeSummary(
            trading_days=window, sample_size=len(raw), warning=sample_warning(len(raw)),
            median_forward_return=median(raw) if raw else None,
            mean_forward_return=mean(raw) if raw else None,
            positive_return_frequency=sum(r > 0 for r in raw) / len(raw) if raw else None,
            median_excess_return=median(excess) if excess else None,
            worst_historical_outcome=min(raw) if raw else None,
            best_historical_outcome=max(raw) if raw else None,
            benchmark_sample_size=len(broad), median_benchmark_return=median(broad) if broad else None,
            benchmark_warning=sample_warning(len(broad)),
        ))
    return tuple(summaries)


class HistoricalAnalogueService:
    def __init__(self, regime_service, target_provider=None, config=None):
        self.regime_service = regime_service
        self.target_provider = target_provider or regime_service.market
        self.config = config or AnalogueConfig()

    def search(self, ticker, as_of, top=10):
        ticker = ticker.strip().upper()
        if not TICKER_PATTERN.fullmatch(ticker):
            raise ValueError("Invalid equity ticker")
        if asset_profile_for(ticker).asset_type != AssetType.EQUITY:
            raise ValueError("Historical analogues currently support listed equities only")
        if not 1 <= top <= 100:
            raise ValueError("Top must be between 1 and 100")
        config = self.config
        start = as_of - timedelta(days=365 * config.history_years + 550)
        histories = self.regime_service.histories(start, as_of)
        try:
            target = ObservationSeries(self.target_provider.market_history(ticker, start, as_of))
        except Exception:
            target = ObservationSeries()
        benchmark = histories.market[self.regime_service.config.benchmarks[0]]
        broad = benchmark.through(as_of)
        # Purge the current feature window from historical outcomes. A maximum-window
        # outcome must end before the earliest close in today's 60-session feature window.
        embargo = 60 + max(config.outcome_windows)
        cutoff = as_of - timedelta(days=365 * config.history_years)
        candidate_dates = [r.date for r in broad[60:-embargo] if r.date >= cutoff]
        asset_sessions = len(target.through(as_of))
        asset_mode = asset_sessions >= config.minimum_asset_sessions
        builder = HistoricalFeatureBuilder(histories, target, ticker, self.regime_service.config)
        current = builder.build(as_of, asset_mode)
        candidates = [builder.build(day, asset_mode) for day in candidate_dates]
        if asset_mode:
            candidates = [c for c in candidates if all(
                c.features.get(key) is not None for key in config.weights
                if key.startswith("target_asset_"))]
            if len(candidates) < config.minimum_asset_candidates:
                asset_mode = False
                current = builder.build(as_of, False)
                candidates = [builder.build(day, False) for day in candidate_dates]
        # Current target evidence must also be complete; otherwise use the explicit fallback.
        if asset_mode and any(current.features.get(k) is None for k in config.weights
                              if k.startswith("target_asset_")):
            asset_mode = False
            current = builder.build(as_of, False)
            candidates = [builder.build(day, False) for day in candidate_dates]

        weights, excluded = {}, {}
        for key, weight in config.weights.items():
            coverage = sum(c.features.get(key) is not None for c in candidates) / max(
                len(candidates), 1)
            if not asset_mode and key.startswith("target_asset_"):
                excluded[key] = "market_regime_only mode"
            elif weight == 0:
                excluded[key] = "zero configured weight"
            elif current.features.get(key) is None:
                excluded[key] = "current feature unavailable or stale"
            elif coverage < config.historical_feature_coverage:
                excluded[key] = f"historical coverage {coverage:.3f} below threshold"
            else:
                weights[key] = weight
        total = sum(weights.values())
        weights = {key: value / total for key, value in weights.items()} if total else {}
        scored = []
        for candidate in candidates:
            score, distance, penalty, coverage = similarity(
                current, candidate, weights, config.scales, config.category_penalty)
            if weights and coverage >= config.minimum_feature_coverage:
                scored.append((score, candidate.as_of, distance, penalty, coverage, candidate))
        # Stable chronological tie break. No hidden preference for recent history or outcomes.
        scored.sort(key=lambda row: (-row[0], row[1]))
        matches = []
        positions = {r.date: i for i, r in enumerate(broad)}
        for score, day, distance, penalty, coverage, candidate in scored:
            if any(abs(positions[day] - positions[m.date]) < config.separation_sessions
                   for m in matches):
                continue
            matches.append(HistoricalAnalogue(
                date=day, similarity=score, feature_distance=distance, category_penalty=penalty,
                feature_coverage=coverage, snapshot=candidate,
                outcomes=forward_outcomes(target, benchmark, day, as_of,
                                          config.outcome_windows, asset_mode),
            ))
            if len(matches) == top:
                break
        return AnalogueSearchResult(
            as_of=as_of, ticker=ticker, benchmark=self.regime_service.config.benchmarks[0],
            mode="asset_specific" if asset_mode else "market_regime_only",
            asset_specific_status="available" if asset_mode else
                                  "insufficient history or consistently available target features",
            current=current, matches=tuple(matches), summaries=summarize(matches, config.outcome_windows),
            sample_size=len(matches), sample_warning=sample_warning(len(matches)),
            candidate_count=len(scored), weights=weights,
            scales={key: config.scales[key] for key in weights}, excluded_features=excluded,
            configuration={**json.loads(json.dumps(asdict(config))), "recency_preference": False,
                           "embargo_sessions": embargo, "normalisation": "fixed configured scales",
                           "outcome_units": "percent close-to-close price returns, not total returns"},
        )
