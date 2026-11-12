"""Allowlisted conversion of existing research evidence; never consumes profiles or AI reports."""

import hashlib
import re
from collections import defaultdict
from datetime import datetime, time, timezone
from urllib.parse import urlsplit

from investment_assistant.domain.assets import asset_profile_for
from investment_assistant.domain.scenarios import (
    ScenarioAsset, ScenarioEvidencePackage, ScenarioEvidenceRef, ScenarioEventCluster,
)

EVENT_TERMS = {
    "earnings": ("earnings", "quarterly results"),
    "monetary_policy": ("federal reserve", "fomc", "central bank"),
    "ai_investment": ("artificial intelligence", "ai investment", "ai spending"),
    "regulation": ("regulat", "antitrust", "investigation"),
    "geopolitics": ("war", "conflict", "geopolitic"),
    "company_operations": ("launch", "contract", "milestone", "production", "operation"),
    "sector_rotation": ("sector rotation", "growth stocks", "technology shares"),
    "valuation_concerns": ("valuation", "price-to", "price to"),
    "economic_growth": ("economic growth", "gdp", "recession", "consumer spending"),
    "inflation": ("inflation", "pce", "consumer price", "producer price"),
    "tariffs_trade": ("tariff", "trade policy"),
}
CURRENT_MAX_AGE = {"market_data": 7, "market_regime": 7, "current_event": 14,
                   "filing": 90, "fundamental": 180}
POSITIVE = {"uptrend", "strong_uptrend", "risk_on", "accelerating"}
NEGATIVE = {"downtrend", "strong_downtrend", "risk_off", "slowing", "contraction",
            "elevated", "stressed"}


def timestamp(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Timezone required")
        return value
    text = str(value)
    if len(text) == 10:
        # Daily/date-only facts are conservatively available at end of the given UTC day.
        return datetime.combine(datetime.fromisoformat(text).date(), time.max, timezone.utc)
    result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result


def event_category(text):
    return next((k for k, terms in EVENT_TERMS.items() if any(t in text.lower() for t in terms)),
                "other")


def _norm(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def cluster_current_events(records):
    groups = defaultdict(list)
    for item in records:
        if item.still_relevant:
            groups[(item.category, item.scope)].append(item)
    clusters = []
    for (category, scope), members in sorted(groups.items()):
        members.sort(key=lambda r: r.reference)
        digest = hashlib.sha256("|".join(r.reference for r in members).encode()).hexdigest()[:12]
        clusters.append(ScenarioEventCluster(
            cluster_id=f"events/{digest}", category=category,
            factual_summary=" ".join(dict.fromkeys(r.summary for r in members)),
            asset_relevance="direct" if scope == "company" else "market_context",
            market_relevance=scope == "market", scope=scope,
            evidence_refs=tuple(r.reference for r in members),
            timestamp=max(r.timestamp for r in members), current=True,
            still_relevant=any(r.decision_grade and r.still_relevant for r in members),
        ))
    return tuple(clusters)


def historical_references(cases):
    refs = []
    for case in cases:
        case_id = f"{case.analogue_date}/{case.benchmark}/{case.ticker}"
        measured = timestamp(str(case.analogue_date))
        refs.append(ScenarioEvidenceRef(reference="history/" + case_id,
            source=f"Saved {case.benchmark} market-regime observations", timestamp=measured,
            available_at=measured, evidence_type="historical_case", asset="US",
            asset_relevance="market_context", scope="market", temporal="historical",
            decision_grade=True, summary=f"Historical US-market case dated {case.analogue_date}.",
            artifact_id=case_id, source_classification="deterministic historical observations"))
        for horizon, outcome in case.historical_outcomes.items():
            if outcome.end_date and outcome.sufficient_benchmark_data:
                measured = timestamp(str(outcome.end_date))
                refs.append(ScenarioEvidenceRef(reference=f"history-outcome/{case_id}/{horizon}",
                    source=f"Saved {case.benchmark} benchmark price observations", timestamp=measured,
                    available_at=measured, evidence_type="historical_outcome", asset="US",
                    asset_relevance="market_context", scope="market", temporal="historical",
                    decision_grade=True, summary=f"Observed {horizon} benchmark price return "
                    f"for the market case dated {case.analogue_date}.", metric=horizon,
                    value=outcome.benchmark_return, unit="percent price return",
                    artifact_id=case_id, source_classification="historical benchmark outcome"))
    return tuple(refs)


def normalise_package(package):
    """Deduplicate and recheck recency even for externally supplied typed packages."""
    rows, exclusions, seen, texts = {}, list(package.exclusions), set(), set()
    records = sorted(package.current_refs(), key=lambda r: (
        not r.decision_grade, "official" not in r.source_classification.lower(),
        -r.available_at.timestamp(), r.reference))
    for record in records:
        url = urlsplit(record.url)._replace(query="", fragment="").geturl() if record.url else None
        identity = (record.evidence_type, record.scope, record.asset, record.metric,
                    url if record.evidence_type in {"current_event", "filing"} and url
                    else _norm(record.summary))
        text_identity = (record.evidence_type, record.scope, record.asset, record.category,
                         _norm(record.summary), record.scheduled_for)
        repeated_text = record.evidence_type in {"current_event", "filing"} and text_identity in texts
        if identity in seen or repeated_text:
            exclusions.append(f"{record.reference}: duplicate evidence")
            continue
        seen.add(identity)
        texts.add(text_identity)
        age = (package.evidence_cutoff - record.timestamp).total_seconds() / 86400
        if age > CURRENT_MAX_AGE[record.evidence_type]:
            exclusions.append(f"{record.reference}: stale; retained for audit only")
            record = record.model_copy(update={"still_relevant": False, "decision_grade": False})
        rows[record.reference] = record
    data = package.model_dump()
    fields = {"current_market_data": "market_data", "current_events": "current_event",
              "regime_evidence": "market_regime", "fundamentals": "fundamental", "filings": "filing"}
    for field, kind in fields.items():
        data[field] = tuple(sorted((r for r in rows.values() if r.evidence_type == kind),
                                   key=lambda r: r.reference))
    data["event_clusters"] = cluster_current_events((*data["current_events"], *data["filings"]))
    data["historical_evidence"] = historical_references(package.historical_cases)
    data["exclusions"] = tuple(dict.fromkeys(exclusions))
    usable = [r for r in rows.values() if r.decision_grade and r.still_relevant]
    gaps = []
    if not any(r.evidence_type == "market_data" for r in usable):
        gaps.append("Recent company-specific price evidence is unavailable.")
    if not any(r.evidence_type == "fundamental" for r in usable):
        gaps.append("Decision-grade current fundamentals are unavailable.")
    if not any(r.known_catalyst and r.scope == "company" and
               r.scheduled_for > package.evidence_cutoff for r in usable):
        gaps.append("No verified upcoming company catalyst is established by the supplied evidence.")
    if not any(r.evidence_type in {"current_event", "filing"} and r.scope == "company" for r in usable):
        gaps.append("Verified current company news or filing summaries are unavailable.")
    if not any(r.evidence_type == "market_regime" for r in usable):
        gaps.append("A current market-regime snapshot is unavailable.")
    if not package.historical_cases:
        gaps.append("Historical case context is unavailable.")
    data["missing_evidence"] = tuple(gaps)
    return ScenarioEvidencePackage.model_validate(data)


class ScenarioEvidenceBuilder:
    def build(self, ticker, cutoff, *, raw_evidence=None, regime=None, historical_cases=(),
              source_artifacts=(), exclusions=()):
        profile = asset_profile_for(ticker)
        buckets = defaultdict(list)
        excluded = list(exclusions)
        raw = raw_evidence or {}

        def add(**record):
            try:
                item = ScenarioEvidenceRef(**record)
                if item.timestamp > cutoff or item.available_at > cutoff:
                    excluded.append(f"{item.reference}: excluded after evidence cutoff")
                    return
                buckets[item.evidence_type].append(item)
            except (ValueError, TypeError):
                excluded.append("An input record was excluded because its metadata was invalid")

        market = raw.get("market_data") or {}
        if market.get("latest_trading_date"):
            try:
                measured = timestamp(market["latest_trading_date"])
                momentum = market.get("five_day_percentage_change")
                if momentum is not None:
                    value = float(momentum)
                    add(reference="asset/momentum", source=str(market.get("source") or
                        "Saved daily market-data provider"), timestamp=measured, available_at=measured,
                        evidence_type="market_data", asset=ticker, asset_relevance="direct",
                        scope="company", decision_grade=True, metric="five_day_return", value=value,
                        unit="percent", summary=f"{ticker}'s recent observed price momentum was " +
                        ("positive." if value > 0 else "negative." if value < 0 else "flat."),
                        direction="positive" if value > 0 else "negative" if value < 0 else "neutral",
                        artifact_id=str(market.get("reference") or "saved market data"))
            except (ValueError, TypeError):
                excluded.append("Asset momentum metadata unavailable")

        for number, item in enumerate(raw.get("ranked_information_items") or []):
            if not isinstance(item, dict):
                continue
            classification = str(item.get("classification") or "")
            depth = str(item.get("evidence_depth") or "headline only")
            if "user-provided" in depth or classification in {"opinion", "analyst commentary"}:
                excluded.append(f"item-{number}: commentary is not scenario fact evidence")
                continue
            summary = item.get("summary_supplied_by_source")
            filing = item.get("filing_metadata") or {}
            if not summary:
                summary = filing.get("material_event_summary")
            official = classification in {"regulatory filing", "official company release"}
            reviewed = bool(item.get("decision_grade") and item.get("summary_reviewed"))
            if not summary:
                # Keep metadata for review, without endorsing the headline as an operating fact.
                summary = "A report is indexed, but a verified factual summary is unavailable."
            else:
                summary = str(summary)[:700]
            try:
                published = timestamp(item["publication_time"])
                available = timestamp(item.get("available_at") or item["publication_time"])
                is_market = item.get("scope") == "market"
                scheduled = timestamp(item["scheduled_for"]) if item.get("scheduled_for") else None
                grade = bool((official or reviewed) and depth != "headline only" and
                             (item.get("summary_supplied_by_source") or
                              filing.get("material_event_summary")))
                add(reference=f"event/{number}", source=str(item.get("source") or "Saved metadata"),
                    timestamp=published, available_at=available, asset=ticker if not is_market else "US",
                    evidence_type="filing" if classification == "regulatory filing" else "current_event",
                    scope="market" if is_market else "company",
                    asset_relevance="market_context" if is_market else "direct",
                    decision_grade=grade, summary=summary,
                    url=item.get("original_publisher_url") or filing.get("official_url"),
                    category=event_category(str(item.get("headline") or "") + " " + summary),
                    direction=item.get("direction", "unknown") if grade else "unknown",
                    known_catalyst=bool(scheduled and grade), scheduled_for=scheduled,
                    source_classification=classification, evidence_depth=depth,
                    artifact_id=item.get("reference"))
            except (ValueError, TypeError, KeyError):
                excluded.append(f"item-{number}: invalid or missing publication metadata")

        fundamentals = raw.get("fundamental_and_valuation_context") or {}
        for number, metric in enumerate(fundamentals.get("decision_grade_metrics") or []):
            try:
                published = timestamp(metric["as_of"])
                name = str(metric["name"]).lower().replace(" ", "_")
                value = float(metric["value"])
                growth = "growth" in name and any(k in name for k in ("revenue", "earnings", "income"))
                direction = "positive" if growth and value > 0 else "negative" if growth and value < 0 \
                    else "neutral"
                qualified = bool(metric.get("decision_grade") and all(metric.get(key) for key in
                    ("source", "reporting_period", "calculation_method", "currency", "unit")))
                available = timestamp(metric.get("available_at") or metric["as_of"])
                if metric.get("price_date"):
                    available = max(available, timestamp(str(metric["price_date"])))
                add(reference=f"fundamental/{number}", source=str(metric.get("source") or "Unknown"),
                    timestamp=published, available_at=available, evidence_type="fundamental", asset=ticker,
                    asset_relevance="direct", scope="company", decision_grade=qualified,
                    metric=name, value=value, unit=str(metric.get("unit") or "unknown"),
                    summary=f"Reported {name.replace('_', ' ')} for {metric.get('reporting_period')} "
                            f"was {value:g} {metric.get('unit', '')}.", direction=direction,
                    source_classification="official fundamental" if metric.get("official_source")
                        else "attributed fundamental", reporting_period=metric.get("reporting_period"),
                    calculation_method=metric.get("calculation_method"), currency=metric.get("currency"))
            except (ValueError, TypeError, KeyError):
                excluded.append(f"fundamental-{number}: incomplete attribution")

        regime_id = None
        if regime is not None:
            if timestamp(str(regime.as_of)) > cutoff:
                excluded.append("Market regime after evidence cutoff was excluded")
                regime = None
            else:
                regime_id = f"regime/{regime.as_of}/{regime.benchmark}/{regime.engine_version}"
                for key, classification in regime.classification.items():
                    dimension = getattr(regime, key)
                    valid = classification != "unknown" and bool(dimension.evidence)
                    source = "; ".join(dict.fromkeys(e.source for e in dimension.evidence))[:200]
                    measured = timestamp(str(regime.as_of))
                    add(reference=f"regime/{key}", source=source or "Saved regime evidence",
                        timestamp=measured, available_at=measured, evidence_type="market_regime",
                        asset="US", asset_relevance="market_context", scope="market",
                        decision_grade=valid, category=key, metric=key, classification=classification,
                        summary=f"{key.replace('_', ' ').capitalize()}: {classification.replace('_', ' ')}.",
                        direction="positive" if classification in POSITIVE else
                            "negative" if classification in NEGATIVE else "neutral",
                        artifact_id=regime_id)
        cases = tuple(c for c in historical_cases if timestamp(str(c.current_as_of)) <= cutoff)
        package = ScenarioEvidencePackage(
            asset=ScenarioAsset(ticker=ticker, name=profile.name, asset_type=profile.asset_type.value),
            evidence_cutoff=cutoff, current_market_data=tuple(buckets["market_data"]),
            current_events=tuple(buckets["current_event"]), market_regime=regime,
            regime_evidence=tuple(buckets["market_regime"]), historical_cases=cases,
            fundamentals=tuple(buckets["fundamental"]), filings=tuple(buckets["filing"]),
            regime_snapshot_id=regime_id,
            historical_case_ids=tuple(f"{c.analogue_date}/{c.benchmark}/{c.ticker}" for c in cases),
            source_artifacts=tuple(source_artifacts), exclusions=tuple(excluded),
        )
        return normalise_package(package)
