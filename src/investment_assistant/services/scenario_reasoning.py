"""Fixed, auditable scenario rules. No price targets, probability calibration or AI transport."""

from datetime import datetime, timezone
from statistics import median, pstdev, quantiles

from investment_assistant.domain.scenarios import (
    HistoricalScenarioSummary, Scenario, ScenarioContradiction, ScenarioDriver, ScenarioSet,
    ScenarioTrigger,
)
from investment_assistant.services.scenario_evidence import NEGATIVE, POSITIVE, normalise_package

PRIORITIES = {
    "20d": {"regime": .32, "momentum": .25, "events": .28, "fundamentals": .08, "historical": .07},
    "60d": {"regime": .22, "momentum": .10, "events": .18, "fundamentals": .40, "historical": .10},
}
MINIMUM_CASES = 5
MINIMUM_SIMILARITY = 80.0
MAXIMUM_DISPERSION = 12.0
MAXIMUM_INTERQUARTILE_RANGE = 15.0
MAXIMUM_RANGE = 40.0
SIGN = {"positive": 1, "negative": -1, "neutral": 0, "unknown": 0}


def historical_summary(package, horizon):
    eligible = []
    for case in package.historical_cases:
        outcome = case.historical_outcomes.get(horizon)
        if (case.similarity_score >= MINIMUM_SIMILARITY and outcome and outcome.end_date
                and outcome.sufficient_benchmark_data and outcome.benchmark_return is not None
                and outcome.end_date <= package.evidence_cutoff.date()):
            eligible.append((case, outcome))
    benchmarks = {c.benchmark for c, _ in eligible}
    # Separate benchmarks are not interchangeable return populations.
    if len(benchmarks) > 1:
        return HistoricalScenarioSummary(horizon=horizon, sample_size=0, independent_sample_size=0,
            historical_dispersion="not comparable", influence="disabled",
            reasons=("Historical benchmark populations differ",), case_ids=(), independent_case_ids=())
    values = [o.benchmark_return for _, o in eligible]
    independent, last_end = [], None
    for case, outcome in sorted(eligible, key=lambda row: row[0].analogue_date):
        if last_end is None or case.analogue_date > last_end:
            independent.append(case)
            last_end = outcome.end_date
    ids = tuple(f"{c.analogue_date}/{c.benchmark}/{c.ticker}" for c, _ in eligible)
    independent_ids = tuple(f"{c.analogue_date}/{c.benchmark}/{c.ticker}" for c in independent)
    dispersion = pstdev(values) if len(values) > 1 else None
    iqr = quantiles(values, n=4, method="inclusive")[2] - quantiles(
        values, n=4, method="inclusive")[0] if len(values) > 1 else None
    reasons = []
    if len(values) < MINIMUM_CASES:
        reasons.append("Too few sufficiently similar cases with available outcomes")
    if len(independent) < MINIMUM_CASES:
        reasons.append("Too few non-overlapping outcome windows")
    if dispersion is not None and (dispersion > MAXIMUM_DISPERSION or
            iqr > MAXIMUM_INTERQUARTILE_RANGE or max(values) - min(values) > MAXIMUM_RANGE):
        reasons.append("Historical outcomes are too dispersed")
    current_trend = package.market_regime.equity_trend.classification if package.market_regime else "unknown"
    direction = -1 if current_trend in NEGATIVE else 1 if current_trend in POSITIVE else 0
    support = tuple(i for i, (_, o) in zip(ids, eligible) if direction * o.benchmark_return > 0)
    contradiction = tuple(i for i, (_, o) in zip(ids, eligible) if direction * o.benchmark_return < 0)
    return HistoricalScenarioSummary(
        horizon=horizon, benchmark=next(iter(benchmarks), None), sample_size=len(values),
        independent_sample_size=len(independent), median_return=median(values) if values else None,
        positive_outcome_frequency=sum(v > 0 for v in values) / len(values) if values else None,
        worst_outcome=min(values) if values else None, best_outcome=max(values) if values else None,
        outcome_dispersion=dispersion, interquartile_range=iqr, historical_support=support,
        historical_contradiction=contradiction,
        historical_dispersion="unavailable" if dispersion is None else
            "dispersed" if any("dispersed" in r for r in reasons) else "within configured bounds",
        influence="disabled" if reasons else "limited_context",
        reasons=tuple(reasons) or ("Similar, sufficiently numerous and non-overlapping benchmark cases; "
                                  "context only, never an asset forecast",),
        case_ids=ids, independent_case_ids=independent_ids,
    )


def _observed_direction(record):
    if record.evidence_type == "market_regime":
        return "positive" if record.classification in POSITIVE else \
            "negative" if record.classification in NEGATIVE else "neutral"
    if record.value is not None and (record.metric == "five_day_return" or
            record.evidence_type == "fundamental" and "growth" in (record.metric or "")):
        return "positive" if record.value > 0 else "negative" if record.value < 0 else "neutral"
    return record.direction


def _observed_driver(record, priorities):
    direction = _observed_direction(record)
    factor = {"market_regime": "regime", "market_data": "momentum", "fundamental": "fundamentals",
              "current_event": "events", "filing": "events"}[record.evidence_type]
    kind, mechanism = "current_observation", ()
    if factor == "regime":
        value = (record.classification or "unknown").replace("_", " ")
        if record.metric == "equity_trend":
            statement = f"Broad equities are in a {value}."
            mechanism = ("Broad market conditions", "risk appetite may change", "asset returns may be affected")
        elif record.metric == "risk_sentiment":
            statement = f"Market sentiment is {value.replace(' ', '-')} .".replace(" .", ".")
        elif record.metric == "rates":
            statement = f"Treasury yields are {value}; their cause is not established by direction alone."
            mechanism = ("Bond yields change", "financing costs and valuations may adjust")
        elif record.metric == "volatility":
            statement = f"Market price swings are {value}."
        else:
            statement = f"The observed {record.metric.replace('_', ' ')} environment is {value}."
    elif factor == "momentum":
        subject = "company-specific" if record.scope == "company" else "broad-market"
        statement = f"Recent {subject} price momentum is {direction}."
        mechanism = ("Recent asset price movement", "near-term positioning may respond")
    elif factor == "fundamentals":
        if "growth" in (record.metric or ""):
            statement = f"Reported {record.metric.replace('_', ' ')} is {direction}."
            mechanism = ("Reported business growth", "future cash-flow expectations may change")
        else:
            statement = "Attributed fundamental data is available; it does not alone establish future performance."
    else:
        if record.known_catalyst:
            kind = "known_catalyst"
            subject = "company" if record.scope == "company" else "market"
            statement = f"A {subject} event is scheduled in the supplied evidence; its result remains open."
        elif direction in {"positive", "negative"}:
            subject = "company" if record.scope == "company" else "market"
            statement = f"A reviewed {subject} update contains {direction} evidence."
        else:
            statement = "A current factual update is documented, but its market direction remains uncertain."
        mechanism = ("New factual information", "expectations may change", "market demand may respond")
    return ScenarioDriver(driver_id=record.reference, statement=statement, factor=factor,
        scope=record.scope, direction=direction, role="context", kind=kind,
        evidence_refs=(record.reference,), internal_weight=priorities[factor], possible_mechanism=mechanism)


def _drivers(package, horizon, stats):
    priorities = PRIORITIES[horizon]
    records = [r for r in package.current_refs() if r.decision_grade and r.still_relevant]
    event_refs = {ref for c in package.event_clusters if c.still_relevant for ref in c.evidence_refs}
    records = [r for r in records if r.evidence_type not in {"current_event", "filing"}
               or r.reference in event_refs]
    rows, seen = [], set()
    for record in records:
        if record.known_catalyst and record.scheduled_for <= package.evidence_cutoff:
            record = record.model_copy(update={"known_catalyst": False})
        # Calendar bands screen timing relevance; they are not future exchange-session forecasts.
        if (record.known_catalyst and (record.scheduled_for - package.evidence_cutoff).days >
                (35 if horizon == "20d" else 100)):
            continue
        driver = _observed_driver(record, priorities)
        # Multiple articles or metrics describing the same signal cannot multiply its vote.
        key = (driver.factor, record.metric if driver.factor in {"regime", "fundamentals"}
               else record.category, driver.scope, driver.direction, driver.kind)
        if key in seen:
            continue
        seen.add(key)
        rows.append(driver)
    if stats.influence == "limited_context":
        direction = "positive" if stats.median_return > 0 else "negative" if stats.median_return < 0 \
            else "neutral"
        statement = ("Comparable historical benchmark cases had mixed outcomes; they provide market "
                     "context, not a company forecast." if stats.historical_support and
                     stats.historical_contradiction else
                     f"Comparable historical benchmark outcomes were {direction} at this horizon; "
                     "this is market context, not a company forecast.")
        rows.append(ScenarioDriver(driver_id=f"history/{horizon}", statement=statement,
            factor="historical", scope="market", direction=direction, role="context",
            kind="historical_context", evidence_refs=tuple(
                f"history-outcome/{i}/{horizon}" for i in stats.case_ids),
            internal_weight=priorities["historical"]))
    return tuple(sorted(rows, key=lambda d: (-d.internal_weight,
        d.direction not in {"positive", "negative"}, d.driver_id)))


def _balance(drivers, priorities):
    by_factor = {}
    for factor in priorities:
        group = [SIGN[d.direction] for d in drivers if d.factor == factor]
        if group:
            by_factor[factor] = sum(group) / len(group)
    current = {f: v for f, v in by_factor.items() if f != "historical"}
    weight = sum(priorities[f] for f in current)
    if not weight:
        return None  # Historical outcomes alone cannot establish the primary asset scenario.
    balance = sum(priorities[f] * v for f, v in current.items()) / weight
    historical_weight = priorities["historical"] if "historical" in by_factor else 0
    # Missing fundamentals must not amplify the historical context allocation.
    return balance * (1 - historical_weight) + historical_weight * by_factor.get("historical", 0)


def _trigger(text, scope="market", refs=()):
    return ScenarioTrigger(text=text, kind="conditional_future_development", scope=scope,
                           evidence_refs=refs)


def _scenario(package, horizon, name, drivers):
    ticker = package.asset.ticker
    balance = _balance(drivers, PRIORITIES[horizon])
    orientation = ("insufficient_evidence" if balance is None else "constructive" if balance > .12
                   else "cautious" if balance < -.12 else "balanced")
    company = [d for d in drivers if d.scope == "company"]
    operating = any(d.factor == "fundamentals" for d in company)
    event_refs = tuple(r for d in company if d.kind == "known_catalyst" for r in d.evidence_refs)
    if name == "base":
        if balance is None:
            summary = (f"Current evidence is too limited for a directional primary scenario for {ticker}. "
                + ("Over twenty trading days, fresh price and event evidence would be needed."
                   if horizon == "20d" else
                   "Over sixty trading days, operating results and the macro environment would be needed."))
        elif horizon == "20d":
            summary = (f"The current evidence supports a {orientation} primary scenario for {ticker} "
                "over twenty trading days if recent market and price conditions continue. " +
                ("Company-specific direction remains unresolved because recent asset evidence is incomplete."
                 if not company else "Current company evidence must be weighed alongside the market backdrop."))
        else:
            summary = (f"Over sixty trading days, the primary scenario for {ticker} remains {orientation}, "
                "with business performance and the macro environment carrying greater emphasis. " +
                ("Missing decision-grade fundamentals limit the company-specific conclusion."
                 if not operating else "The supplied business evidence remains important to this continuation."))
        conditions = (_trigger("The broad market environment does not change materially."),
            _trigger("New company information does not materially alter the operating picture.", "company"))
        invalidations = (_trigger("A sustained change in market breadth and risk appetite develops."),
            _trigger("Verified company results materially differ from the current evidence.", "company"))
    elif name == "bull":
        orientation = "conditional_upside"
        summary = (f"A stronger near-term outcome for {ticker} becomes more plausible if risk appetite "
                   "improves and company-specific momentum or verified event evidence strengthens."
                   if horizon == "20d" else
                   f"A stronger medium-term outcome for {ticker} becomes more plausible if verified "
                   "business progress is sustained and the macro or sector backdrop becomes more supportive.")
        conditions = (_trigger("Market breadth and risk appetite improve."),
            _trigger("Fresh company evidence supports operating progress.", "company"))
        if event_refs:
            conditions += (_trigger("The documented scheduled event delivers stronger operating evidence.",
                                     "company", event_refs),)
        invalidations = (_trigger("Market weakness persists despite attempted recoveries."),
            _trigger("Verified operating evidence fails to improve.", "company"))
    else:
        orientation = "conditional_downside"
        summary = (f"A weaker near-term outcome for {ticker} becomes more plausible if risk appetite "
                   "deteriorates and company momentum or new factual evidence weakens."
                   if horizon == "20d" else
                   f"A weaker medium-term outcome for {ticker} becomes more plausible if operating "
                   "progress deteriorates or persistent macro pressure weighs on business expectations.")
        conditions = (_trigger("Broad market pressure persists or becomes more severe."),
            _trigger("Verified company evidence deteriorates or fails to offset market pressure.", "company"))
        invalidations = (_trigger("A sustained market recovery develops."),
            _trigger("Strong verified business progress counters the downside conditions.", "company"))
    if horizon == "60d":
        if name == "base":
            conditions = (_trigger("The macro and sector environment remains broadly consistent."),
                _trigger("New business data does not materially alter the operating picture.", "company"))
            invalidations = (_trigger("Macro conditions shift persistently."),
                _trigger("A material change in business growth or financial resilience is verified.", "company"))
        elif name == "bull":
            conditions = (_trigger("Financing and sector conditions become more supportive."),
                _trigger("Verified operating results show sustained progress.", "company"))
            if event_refs:
                conditions += (_trigger("The documented catalyst leads to sustained operating progress.",
                                         "company", event_refs),)
            invalidations = (_trigger("Macro financing pressure intensifies."),
                _trigger("Later business evidence fails to sustain operating progress.", "company"))
        else:
            conditions = (_trigger("Prolonged macro pressure affects business demand."),
                _trigger("Verified business results deteriorate.", "company"))
            invalidations = (_trigger("Macro and sector conditions improve persistently."),
                _trigger("Sustained business progress counters the downside conditions.", "company"))
    assigned = []
    for driver in drivers:
        restrains = name == "bull" and driver.direction == "negative" or \
            name == "bear" and driver.direction == "positive"
        if name == "base":
            restrains = orientation == "cautious" and driver.direction == "positive" or \
                orientation == "constructive" and driver.direction == "negative"
        role = "restrains" if restrains else "supports" if driver.direction in {"positive", "negative"} \
            else "context"
        if name == "base" and orientation in {"balanced", "insufficient_evidence"}:
            role = "context"
        assigned.append(driver.model_copy(update={"role": role}))
    # Each scenario keeps both supportive and restraining observations, not just one side.
    refs = tuple(dict.fromkeys(r for d in assigned if d.kind != "historical_context"
                              for r in d.evidence_refs))
    histories = tuple(dict.fromkeys(r for d in assigned if d.kind == "historical_context"
                                   for r in d.evidence_refs))
    return Scenario(name=name, horizon=horizon, summary=summary, orientation=orientation,
        drivers=tuple(assigned), supporting_evidence=refs, historical_context=histories,
        conditions=conditions, invalidation_triggers=invalidations)


class ScenarioReasoningService:
    def generate(self, evidence, *, generated_at=None):
        package = normalise_package(evidence)
        stats, scenarios = {}, []
        for horizon in ("20d", "60d"):
            stats[horizon] = historical_summary(package, horizon)
            drivers = _drivers(package, horizon, stats[horizon])
            scenarios.extend(_scenario(package, horizon, name, drivers) for name in ("base", "bull", "bear"))
        all_drivers = {d.driver_id: d for s in scenarios for d in s.drivers}
        positive = tuple(r for d in all_drivers.values() if d.direction == "positive"
                         for r in d.evidence_refs)
        negative = tuple(r for d in all_drivers.values() if d.direction == "negative"
                         for r in d.evidence_refs)
        contradictions = (ScenarioContradiction(positive_refs=tuple(dict.fromkeys(positive)),
            negative_refs=tuple(dict.fromkeys(negative)), explanation=
            "Positive and negative observations coexist; the primary scenario balances them "
            "rather than treating either side as a certain outcome."),) if positive and negative else ()
        return ScenarioSet(scenario_date=package.evidence_cutoff.date(), asset=package.asset,
            generated_at=generated_at or datetime.now(timezone.utc), evidence_cutoff=package.evidence_cutoff,
            evidence_state=package, scenarios=tuple(scenarios), historical_summaries=stats,
            contradictory_evidence=contradictions, internal_priorities=PRIORITIES,
            regime_snapshot_id=package.regime_snapshot_id, historical_case_ids=package.historical_case_ids)

    def validate(self, result):
        """A deterministic release accepts only claims reproducible from its supplied evidence.

        This also prevents a modified artifact from silently adding an invented catalyst or a
        summary with the opposite direction. A future reasoner will need its own claim validator.
        """
        expected = self.generate(result.evidence_state, generated_at=result.generated_at)
        if result.model_dump() != expected.model_dump():
            raise ValueError("Scenario output is not supported by the deterministic evidence rules")
        return result
