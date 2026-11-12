"""Deterministic historical narratives. No scoring, article scraping, or language-model calls."""

import json
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from investment_assistant.adapters.news.historical_metadata import PublicHistoricalMetadataAdapter
from investment_assistant.domain.historical_context import (
    CaseOutcome, CaseStatement, EventCluster, HistoricalEvent, HistoricalMarketCase,
    HistoricalMarketContext, MarketMechanism,
)
from investment_assistant.services.news_service import TICKER_PATTERN

EVIDENCE_VERSION = "historical-context-v1"
SOURCE_ORDER = {"official_policy": 0, "official_data": 1, "wire": 2,
                "financial_news": 3, "aggregation": 4}
CLUSTER_TITLES = {
    "tariffs_trade": "Tariffs and trade policy", "economic_growth": "Growth and consumer spending",
    "technology": "Technology shares and AI", "geopolitics": "Conflict and global trade risks",
    "inflation": "Inflation and price pressure", "monetary_policy": "Central-bank decisions",
    "interest_rates": "Treasury yields and borrowing costs", "credit_stress": "Credit-market stress",
}

# Conditional mechanisms explain transmission, not a claim to have identified a certain cause.
MECHANISMS = {
    "tariffs_trade": (
        "Trade-policy changes", "uncertain import costs and supply chains",
        "less predictable business profits and growth", "investors reassess equity risk",
    ),
    "inflation": (
        "New price data", "investors reassess inflation", "expected central-bank rates change",
        "bond yields and equity valuations may adjust",
    ),
    "economic_growth": (
        "New growth or spending data", "expected business sales change",
        "profit expectations and demand for safer assets may shift",
        "equities and Treasury yields can move together or apart",
    ),
    "technology": (
        "Technology-sector developments", "expected future profits are reassessed",
        "highly valued growth shares can move sharply", "large index weights spread the impact",
    ),
    "earnings": (
        "Company results and guidance", "expected profits are reassessed",
        "share prices adjust relative to prior expectations", "large companies influence indexes",
    ),
    "geopolitics": (
        "Geopolitical conflict", "possible disruption to trade and energy supply",
        "inflation and growth risks can rise together", "stocks and bonds may both face pressure",
    ),
    "energy": (
        "Energy-supply or demand changes", "fuel costs and producer revenues change",
        "consumer spending power and inflation expectations adjust",
        "sector returns and Treasury yields may diverge",
    ),
    "interest_rates": (
        "Treasury-market developments", "bond prices and yields move in opposite directions",
        "financing costs and the value of future profits change", "equity valuations may adjust",
    ),
    "monetary_policy": (
        "Central-bank decisions or guidance", "expected borrowing costs change",
        "credit demand and valuations adjust", "equity and bond prices may respond",
    ),
    "employment": (
        "Jobs and wage data", "expected household income and spending change",
        "growth and inflation expectations adjust", "expected policy rates may change",
    ),
    "credit_stress": (
        "Credit stress", "lenders demand more compensation for risk",
        "borrowing becomes harder or more expensive", "business investment may weaken",
    ),
    "banking": (
        "Banking developments", "deposit and lending conditions change",
        "credit availability may shift", "business activity and risk appetite may respond",
    ),
    "commodity_shock": (
        "Commodity supply or demand shock", "input costs change",
        "profit margins and consumer prices adjust", "sector performance may diverge",
    ),
    "fiscal_policy": (
        "Government tax or spending changes", "demand and borrowing requirements change",
        "growth and bond-supply expectations adjust", "equities and yields may respond",
    ),
    "regulation": (
        "Regulatory developments", "business rules and compliance costs change",
        "expected profits are reassessed", "affected sectors may reprice",
    ),
    "market_structure": (
        "Changes in market liquidity or trading rules", "execution and funding conditions change",
        "forced buying or selling may amplify moves", "prices may diverge from business news",
    ),
    "sector_rotation": (
        "Investors change sector allocations", "buying and selling pressure shifts",
        "sector performance diverges", "index returns depend on sector weights",
    ),
}
SCENARIOS = {
    "weak_spending": (
        "Weaker consumer spending", "concerns about future company sales",
        "investors may seek safer Treasury bonds", "bond prices rise and yields fall",
        "shares can weaken despite lower yields",
    ),
    "growth_expansion": (
        "Economic output still expanding", "support for company sales and earnings",
        "less urgency for interest-rate cuts", "growth support can compete with valuation pressure",
    ),
    "energy_inflation": (
        "Conflict threatens energy supplies", "oil prices rise", "inflation concerns increase",
        "investors may demand higher bond yields", "cost pressures can weigh on shares",
    ),
    "technology_expectations": (
        "Technology results disappoint high expectations", "expected returns are reassessed",
        "investors reduce exposure to growth shares", "large technology weights pull on indexes",
    ),
}
MECHANISM_EXPLANATIONS = {
    "weak_spending": "Weaker household spending can make investors worry about company sales. "
        "Some may move money into safer government bonds, pushing bond prices up and yields "
        "down. Stocks can still struggle because lower yields do not remove the growth concern.",
    "growth_expansion": "An expanding economy can support company sales. At the same time, "
        "resilient growth can reduce the urgency for rate cuts, so the effect on shares depends "
        "on the balance between earnings support and borrowing costs.",
    "technology_expectations": "Investors compare results with expectations already built into "
        "share prices. Even strong sales can disappoint a market expecting more. Selling in "
        "large technology companies can then pull the broader index lower.",
    "energy_inflation": "Threats to energy supplies can lift oil prices and business costs. "
        "Investors may then demand higher bond yields to compensate for inflation, while "
        "companies face pressure on profits. Stocks and bonds can both lose value.",
    "tariffs_trade": "Tariffs can raise import costs and make future profits harder to predict. "
        "Businesses may delay investment while investors reassess growth and equity risk. "
        "Demand for safer bonds may rise, although inflation concerns can pull yields the other way.",
    "inflation": "Price releases influence expectations for central-bank interest rates. "
        "Persistent inflation can keep borrowing costs high; cooling inflation can ease that "
        "pressure. A price release alone does not establish why the whole market moved.",
}
LESSONS = {
    "growth_expansion": "An economy can still be growing while investors worry about what "
                        "comes next. GDP describes an earlier period; new policy risks can "
                        "change expectations before they appear in the growth figures.",
    "tariffs_trade": "A tariff can raise a company's costs before it changes reported profits. "
                     "Policy uncertainty can therefore move share prices ahead of earnings.",
    "inflation": "Inflation news affects both purchasing power and expected interest rates. "
                 "A single price report does not determine the direction of the stock market.",
    "economic_growth": "Falling Treasury yields do not always help shares. If investors worry "
                       "about growth, they may buy safer bonds while reducing stock exposure.",
    "technology": "Strong sales alone do not guarantee a rising share price. Investors compare "
                  "results with the expectations already built into the price.",
    "earnings": "A profitable company can still fall after good results if investors expected "
                "even more. Price and business performance are related but different.",
    "geopolitics": "Government bonds do not always offset stock losses. An energy-driven "
                   "inflation scare can push bond yields up while shares fall.",
}
REGIME_TEXT = {
    "equity_trend": {
        "downtrend": "Broad equities were in a downtrend",
        "strong_downtrend": "Broad equities were in a strong downtrend",
        "uptrend": "Broad equities were in an uptrend",
        "strong_uptrend": "Broad equities were in a strong uptrend",
        "sideways": "Broad equities were moving sideways",
    },
    "rates": {"falling": "Treasury yields were falling", "rising": "Treasury yields were rising",
              "stable": "Treasury yields were broadly stable"},
    "risk_sentiment": {"risk_off": "Investors were reducing exposure to risky assets",
                       "risk_on": "Investors were favouring risky assets",
                       "neutral": "Risk appetite was mixed"},
    "volatility": {"stressed": "Market price swings were unusually large",
                   "elevated": "Market price swings were elevated",
                   "normal": "Market price swings were within their usual range",
                   "low": "Market price swings were subdued"},
}


def _normal(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _tokens(text):
    return set(_normal(text).split()) - {"the", "and", "for", "with", "from", "that", "this"}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as stream:
            name = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


class HistoricalContextService:
    def __init__(self, root, provider=None, window_days=7):
        if not 0 <= window_days <= 31:
            raise ValueError("Historical window must be between 0 and 31 calendar days")
        self.root = Path(root)
        self.provider = provider or PublicHistoricalMetadataAdapter()
        self.window_days = window_days

    def _path(self, day):
        # Market/version/window are checked in the envelope before reuse.
        return self.root / "historical_cases" / f"{day}.json"

    def historical_context(self, day, *, retrieve=False, persist=False, refresh=False):
        path = self._path(day)
        if not refresh:
            try:
                cached = HistoricalMarketContext.model_validate_json(path.read_text("utf-8"))
                if (cached.analogue_date == day and cached.market == "US"
                        and cached.evidence_version == EVIDENCE_VERSION
                        and cached.window_days == self.window_days
                        and (not retrieve or cached.evidence_refs)):
                    return cached
            except (OSError, ValueError):
                pass
        start = day - timedelta(days=self.window_days)
        status = "reviewed public metadata only; historical archive coverage is partial"
        if retrieve:
            try:
                rows = self.provider.fetch_window(start, day)
                status = "public metadata search completed; archive coverage is partial"
            except (OSError, ValueError):
                rows = ()
                status = "historical metadata retrieval unavailable"
        else:
            offline = getattr(self.provider, "offline_window", None)
            rows = offline(start, day) if offline else ()
        context = self._assemble(day, rows, status)
        # Never persist a failed/empty lookup: a later request must be able to retry.
        if persist and context.evidence_refs:
            atomic_json(path, context.model_dump(mode="json"))
        return context

    def _assemble(self, day, rows, status):
        start = datetime.combine(day - timedelta(days=self.window_days), time.min, timezone.utc)
        end = datetime.combine(day, time.max, timezone.utc)
        eligible = [s for s in rows if start <= s.published_at <= end]
        eligible.sort(key=lambda s: (SOURCE_ORDER[s.source_classification],
                                     not s.reviewed_summary, s.published_at, s.reference))
        unique = []
        urls, headlines, refs = set(), [], set()
        for source in eligible:
            title = _normal(source.headline)
            words = _tokens(title)
            duplicate = any(title == t or (len(words) >= 5 and
                            len(words & w) / max(len(words | w), 1) >= .85)
                            for t, w in headlines)
            url = source.url.split("?")[0].rstrip("/")
            if duplicate or url in urls or source.reference in refs:
                continue
            unique.append(source)
            headlines.append((title, words))
            urls.add(url)
            refs.add(source.reference)
        groups = defaultdict(list)
        for source in unique[:40]:
            groups[source.category].append(source)
        # Source order first; corroboration is a tie-break, never later market performance.
        categories = sorted(groups, key=lambda c: (
            min(SOURCE_ORDER[s.source_classification] for s in groups[c]),
            not any(s.reviewed_summary for s in groups[c]), -len(groups[c]), c.value))[:5]
        clusters, events, sources = [], [], []
        for category in categories:
            evidence = groups[category][:5]
            references = tuple(s.reference for s in evidence)
            reviewed = [s for s in evidence if s.reviewed_summary and s.summary]
            facts = tuple(CaseStatement(kind="FACT", text=s.summary, evidence_refs=(s.reference,))
                          for s in reviewed)
            mechanism = None
            if reviewed and category in MECHANISMS:
                scenario = next((s.mechanism_scenario for s in reviewed if s.mechanism_scenario), None)
                steps = SCENARIOS.get(scenario, MECHANISMS[category])
                mechanism = MarketMechanism(
                    category=category, steps=steps, evidence_refs=tuple(s.reference for s in reviewed),
                    scenario=scenario,
                    explanation="Possible explanation: " + MECHANISM_EXPLANATIONS.get(
                        scenario, MECHANISM_EXPLANATIONS.get(category,
                        "This development can affect business costs, financing or expected "
                        "profits through the steps below. The evidence does not establish "
                        "a single certain cause of the market move.")),
                )
            clusters.append(EventCluster(
                category=category, title=CLUSTER_TITLES.get(
                    category, category.value.replace("_", " ").capitalize()),
                facts=facts, evidence_refs=references, interpretation=mechanism,
            ))
            for source in evidence:
                statement = next((f for f in facts if source.reference in f.evidence_refs), None)
                events.append(HistoricalEvent(
                    event_date=source.published_at.astimezone(timezone.utc).date(), category=category,
                    source_ref=source.reference, affected_markets=source.entities or ("US market",),
                    evidence=statement or CaseStatement(
                        kind="FACT", text=f"{source.publisher} published a report on "
                                          f"{category.value.replace('_', ' ')}; "
                                          "its underlying claims have not been reviewed.",
                        evidence_refs=(source.reference,)),
                    possible_market_mechanism=mechanism if statement else None,
                ))
            sources.extend(evidence)
        sources.sort(key=lambda s: (SOURCE_ORDER[s.source_classification], s.published_at,
                                    s.reference))
        return HistoricalMarketContext(
            analogue_date=day, window_days=self.window_days, main_events=tuple(events),
            event_clusters=tuple(clusters), evidence_refs=tuple(sources), retrieval_status=status,
            source_count=len(sources), evidence_quality=(
                "reviewed summaries with linked public metadata" if any(s.reviewed_summary
                 for s in sources) else "unreviewed metadata only"),
        )

    def build_case(self, result, match, *, current_evidence=(), retrieve=False,
                   persist=False, refresh=False):
        if match.date > result.as_of or match.snapshot.as_of != match.date:
            raise ValueError("Invalid analogue cutoff")
        context = self.historical_context(match.date, retrieve=retrieve,
                                          persist=persist, refresh=refresh)
        historical = match.snapshot.regime.classification
        current = result.current.regime.classification
        similarities, differences, conditions, used_current = [], [], [], {}
        for key, labels in REGIME_TEXT.items():
            old, now = historical[key], current[key]
            if old in labels:
                conditions.append(CaseStatement(kind="FACT", text=labels[old] + "."))
            if old == "unknown" or now == "unknown":
                continue
            if old == now and old in labels and old not in {"normal", "low", "neutral", "stable",
                                                            "sideways"}:
                similarities.append(CaseStatement(kind="COMPARISON", text=
                    labels[old].replace("were", "are").replace("was", "is") +
                    " in both snapshots."))
            elif old != now:
                differences.append(CaseStatement(kind="COMPARISON", text=
                    f"{key.replace('_', ' ')}: historical {old}; current {now}."))
        # Event parallels need reviewed, recent evidence, shared specific entities and content.
        for source in context.evidence_refs:
            for today in current_evidence:
                if (not today.reviewed_summary or not source.reviewed_summary
                        or not result.as_of - timedelta(days=7) <=
                        today.published_at.astimezone(timezone.utc).date() <= result.as_of
                        or source.category != today.category):
                    continue
                entities = set(source.entities) & set(today.entities) - {"US market", "US", "SPY"}
                if entities and len(_tokens(source.summary or "") &
                                    _tokens(today.summary or "")) >= 3:
                    similarities.append(CaseStatement(kind="COMPARISON", text=
                        f"Both evidence windows discuss {source.category.value.replace('_', ' ')} "
                        f"affecting {', '.join(sorted(entities))}; this does not establish "
                        "that the effects have the same direction.",
                        evidence_refs=(source.reference, today.reference)))
                    used_current[today.reference] = today
        mechanisms = tuple(c.interpretation for c in context.event_clusters if c.interpretation)
        lessons = tuple(CaseStatement(kind="LESSON", text=LESSONS.get(m.scenario, LESSONS.get(m.category,
            "Follow how this development changes costs, financing or expected profits. "
            "The same market label can arise through different economic channels.")),
            evidence_refs=m.evidence_refs) for m in mechanisms[:3])
        if not lessons:
            lessons = (CaseStatement(kind="LESSON", text=
                "Matching market conditions do not establish a shared cause. Without verified "
                "event evidence, use this case to compare observations and avoid a causal story."),)
        by_window = {o.trading_days: o for o in match.outcomes}
        outcomes = {}
        # Outcomes enter only after narrative, mechanisms, comparisons, lessons and quality.
        for window in (20, 60):
            outcome = by_window.get(window)
            valid = bool(outcome and outcome.end and match.date < outcome.end <= result.as_of)
            broad_ok = valid and outcome.benchmark_return is not None
            asset_ok = (valid and result.mode == "asset_specific"
                        and outcome.raw_return is not None)
            outcomes[f"{window}d"] = CaseOutcome(
                trading_days=window, start_date=match.date,
                end_date=outcome.end if valid else None,
                benchmark_return=outcome.benchmark_return if broad_ok else None,
                benchmark_maximum_drawdown=outcome.benchmark_maximum_drawdown if broad_ok else None,
                asset_return=outcome.raw_return if asset_ok else None,
                benchmark_excess_return=outcome.excess_return if asset_ok and broad_ok else None,
                maximum_drawdown=outcome.maximum_drawdown if asset_ok else None,
                sufficient_asset_data=bool(asset_ok), sufficient_benchmark_data=bool(broad_ok),
            )
        benchmark = {"SPY": "S&P 500 (SPY price proxy)", "QQQ": "Nasdaq-100 (QQQ price proxy)"}.get(
            result.benchmark, result.benchmark)
        return HistoricalMarketCase(
            analogue_date=match.date, current_as_of=result.as_of, similarity_score=match.similarity,
            ticker=result.ticker, benchmark=result.benchmark, benchmark_label=benchmark,
            mode=result.mode, scope_notice=(
                f"This is a historical US-market case, not a historical {result.ticker} case."
                if result.mode == "market_regime_only" else
                f"This case compares {result.ticker} and its market benchmark."),
            regime=historical, context_before_date=context, macro_conditions=tuple(conditions),
            market_mechanisms=mechanisms, historical_outcomes=outcomes,
            similarities_to_today=tuple({s.text: s for s in similarities}.values())[:5],
            internal_differences_from_today=tuple(differences), lessons=lessons,
            evidence_refs=context.evidence_refs,
            current_evidence_refs=tuple(used_current.values()),
        )

    def save_case(self, case):
        # Current comparison and outcomes never enter the shared historical-context cache.
        path = (self.root / "historical_case_comparisons" / str(case.current_as_of)
                / case.ticker / f"{case.analogue_date}-{case.benchmark}.json")
        if not TICKER_PATTERN.fullmatch(case.ticker) or not TICKER_PATTERN.fullmatch(case.benchmark):
            raise ValueError("Invalid case asset identifier")
        atomic_json(path, case.model_dump(mode="json"))
        return path
