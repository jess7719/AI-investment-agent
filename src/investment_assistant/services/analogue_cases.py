"""Deterministic case cards from saved facts. No providers, LLMs or ranking changes."""

from investment_assistant.domain.analogue_cases import (
    AnalogueCaseCard, CaseFeatureComparison, CaseForwardOutcome, CaseRegimeDimension,
)
from investment_assistant.domain.analogues import AnalogueSearchResult, HistoricalAnalogue

# Display labels and the close/different boundary do not participate in scoring.
MATCHING_DISTANCE_MAXIMUM = 0.20
MAIN_FEATURE_COUNT = 3
FEATURE_LABELS = {
    "broad_market_5d_return": "Broad-market 5-day return",
    "broad_market_20d_return": "Broad-market 20-day return",
    "broad_market_60d_return": "Broad-market 60-day return",
    "volatility_level": "Volatility index level",
    "volatility_percentile": "Volatility percentile",
    "10y_yield_level": "10-year yield level",
    "10y_yield_20d_change": "10-year yield 20-day change",
    "risk_sentiment_score": "Risk sentiment score",
    "target_asset_20d_momentum": "Target-asset 20-day momentum",
    "target_asset_60d_momentum": "Target-asset 60-day momentum",
    "target_asset_realised_volatility": "Target-asset realised volatility",
    "target_asset_volume_ratio": "Target-asset volume ratio",
}
REGIME_LABELS = {
    "equity_trend": "Equity trend", "volatility": "Volatility regime",
    "rates": "Rates direction", "monetary_policy": "Monetary policy regime",
    "inflation": "Inflation regime", "growth": "Growth regime",
    "risk_sentiment": "Risk sentiment",
}


def _readable(value):
    return value.replace("_", " ")


def _value(feature, value):
    if value is None:
        return "unavailable"
    if feature == "volatility_percentile":
        return f"{value:.2f}th percentile"
    if feature == "10y_yield_20d_change":
        return f"{value:+.2f} percentage points"
    if feature == "risk_sentiment_score":
        return f"{value:+.2f}"
    if feature == "volatility_level":
        return f"{value:.2f} index points"
    if feature == "target_asset_volume_ratio":
        return f"{value:.2f}x average volume"
    if feature in {"broad_market_5d_return", "broad_market_20d_return", "broad_market_60d_return",
                   "10y_yield_level", "target_asset_20d_momentum", "target_asset_60d_momentum",
                   "target_asset_realised_volatility"}:
        return f"{value:+.2f}%"
    return f"{value:+.2f}"


def _feature_comparisons(result, match):
    comparisons = []
    for feature, weight in result.weights.items():
        current = result.current.features.get(feature)
        historical = match.snapshot.features.get(feature)
        scale = result.scales[feature]
        distance = (1.0 if current is None or historical is None else
                    min(abs(current - historical) / scale, 1.0))
        status = ("unavailable" if current is None or historical is None else
                  "matching" if distance <= MATCHING_DISTANCE_MAXIMUM else "mismatching")
        label = FEATURE_LABELS.get(feature, _readable(feature).capitalize())
        description = (f"{label}: historical {_value(feature, historical)}; "
                       f"current {_value(feature, current)}; scoring weight {weight:.1%}.")
        if status == "unavailable":
            description += " Missing comparison; the existing score applies a missing-data penalty."
        else:
            description += f" Difference uses {distance:.1%} of its configured distance scale."
        comparisons.append(CaseFeatureComparison(
            feature=feature, label=label, historical=historical, current=current,
            weight=weight, scale=scale, normalized_distance=distance,
            weighted_distance=weight * distance, status=status, explanation=description,
        ))
    return tuple(comparisons)


def _lesson(benchmark, outcomes):
    returns = [o.benchmark_return for o in outcomes if o.benchmark_return is not None]
    if len(returns) == 2 and all(r < 0 for r in returns):
        text = (f"In this case, {benchmark} fell over both 5 and 20 trading days. "
                "Similar observed conditions coexisted with near-term weakness.")
    elif len(returns) == 2 and all(r > 0 for r in returns):
        text = (f"In this case, {benchmark} rose over both 5 and 20 trading days. "
                "Similar observed conditions coexisted with near-term gains.")
    elif len(returns) == 2 and all(r == 0 for r in returns):
        text = f"In this case, {benchmark} ended both measured windows unchanged."
    elif len(returns) == 2:
        text = (f"In this case, {benchmark}'s 5-day and 20-day outcomes differed in direction "
                "or included a flat return. The lesson depends on the holding window.")
    elif returns:
        text = (f"Only one requested {benchmark} outcome window is available. "
                "This case cannot support a comparison across both holding windows.")
    else:
        text = ("The requested forward outcomes are unavailable. "
                "This case describes similar conditions but cannot show their subsequent returns.")
    if any(o.benchmark_return is not None and o.benchmark_return > 0
           and o.benchmark_drawdown is not None and o.benchmark_drawdown < 0 for o in outcomes):
        text += " A positive endpoint return still included a decline from a running peak."
    return text + " This is one historical observation, not a forecast or a causal explanation."


def build_case_card(result: AnalogueSearchResult, match: HistoricalAnalogue) -> AnalogueCaseCard:
    similarities, differences, unavailable, dimensions = [], [], [], []
    for key, label in REGIME_LABELS.items():
        historical = getattr(match.snapshot.regime, key)
        current = getattr(result.current.regime, key)
        dimensions.append(CaseRegimeDimension(
            name=label, historical=_readable(historical.classification),
            current=_readable(current.classification),
            historical_confidence=historical.confidence, current_confidence=current.confidence,
        ))
        if "unknown" in (historical.classification, current.classification):
            when = ("both dates" if historical.classification == current.classification else
                    "the historical date" if historical.classification == "unknown" else
                    "the current date")
            unavailable.append(f"{label} unavailable for {when}; it is not evidence of a match.")
        elif historical.classification == current.classification:
            similarities.append(f"Similar {label.lower()}: {_readable(current.classification)}.")
        else:
            differences.append(f"{label}: historical {_readable(historical.classification)} "
                               f"versus current {_readable(current.classification)}.")

    comparisons = _feature_comparisons(result, match)
    matching = tuple(sorted((c for c in comparisons if c.status == "matching"),
                            key=lambda c: (-c.weight, c.normalized_distance, c.feature))
                     [:MAIN_FEATURE_COUNT])
    mismatching = tuple(sorted((c for c in comparisons if c.status == "mismatching"),
                               key=lambda c: (-c.weighted_distance, c.feature))[:MAIN_FEATURE_COUNT])
    unavailable.extend(c.explanation for c in comparisons if c.status == "unavailable")
    for feature, reason in sorted(result.excluded_features.items()):
        label = FEATURE_LABELS.get(feature, _readable(feature).capitalize())
        if reason == "market_regime_only mode":
            unavailable.append(f"{label} unavailable in market-regime-only mode; not scored.")
        else:
            unavailable.append(f"{label} not scored: {reason}.")

    if similarities:
        why_similar = (f"{len(similarities)} known regime dimensions match the current snapshot "
                       f"as of {result.as_of}.")
    else:
        why_similar = "No known regime classifications match; inspect the numerical comparisons."
    if matching:
        why_similar += " Main close numerical features: " + ", ".join(c.label for c in matching) + "."
    elif comparisons:
        why_similar += " No scored numerical feature falls within the display's close-match band."
    else:
        why_similar += " No scored numerical comparisons are available."
    why_similar += (f" The saved similarity is {match.similarity:.1f}/100; it is a comparison "
                    "score, not a probability of the same outcome.")

    by_window = {o.trading_days: o for o in match.outcomes}
    outcomes = tuple(CaseForwardOutcome(
        trading_days=window, end=by_window[window].end if window in by_window else None,
        benchmark_return=by_window[window].benchmark_return if window in by_window else None,
        benchmark_drawdown=(by_window[window].benchmark_maximum_drawdown
                            if window in by_window else None),
        asset_return=by_window[window].raw_return if window in by_window else None,
        excess_return=by_window[window].excess_return if window in by_window else None,
    ) for window in (5, 20))

    limitations = []
    if result.mode == "market_regime_only":
        limitations.append(f"This is a market-level analogue, not a {result.ticker} "
                           "company-specific analogue. Company returns cannot be inferred "
                           f"from {result.benchmark}'s outcomes.")
    else:
        limitations.append("The feature vector does not capture all company events, business "
                           "changes or valuations between the two dates.")
    if differences or mismatching:
        limitations.append("The differing regimes and numerical features above may matter "
                           "despite a high overall similarity score.")
    if unavailable:
        limitations.append("Unavailable or excluded evidence above limits what can be compared; "
                           "unknown values are not confirmation of similar conditions.")
    if any(d.historical_confidence in {"low", "unknown"}
           or d.current_confidence in {"low", "unknown"} for d in dimensions):
        limitations.append("Some regime dimensions have low or unknown confidence on one or "
                           "both dates; matching labels do not remove that uncertainty.")
    limitations.append(f"Selected analogue set: n={result.sample_size}; {result.sample_warning}. "
                       "One case cannot establish today's outcome probabilities.")
    limitations.append("Similarity does not establish causation or imply the same future result.")
    return AnalogueCaseCard(
        historical_date=match.date, current_as_of=result.as_of, similarity=match.similarity,
        benchmark=result.benchmark, ticker=result.ticker, mode=result.mode,
        historical_regime=tuple(dimensions), why_similar=why_similar,
        similarities=tuple(similarities), differences=tuple(differences),
        matching_features=matching, mismatching_features=mismatching,
        feature_comparisons=comparisons, unavailable_evidence=tuple(unavailable), outcomes=outcomes,
        what_this_case_can_teach_us=_lesson(result.benchmark, outcomes),
        why_this_case_may_not_apply_today=tuple(limitations), sample_warning=result.sample_warning,
    )


def build_case_cards(result: AnalogueSearchResult) -> tuple[AnalogueCaseCard, ...]:
    """Preserve the saved top-match order and scores; never call a search or provider."""
    return tuple(build_case_card(result, match) for match in result.matches)
