"""Historical evidence and interpretations, isolated from ranking and future outcomes."""

from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from investment_assistant.domain.regime import RegimeModel


class EventCategory(StrEnum):
    MONETARY_POLICY = "monetary_policy"
    INTEREST_RATES = "interest_rates"
    INFLATION = "inflation"
    EMPLOYMENT = "employment"
    ECONOMIC_GROWTH = "economic_growth"
    TARIFFS_TRADE = "tariffs_trade"
    GEOPOLITICS = "geopolitics"
    EARNINGS = "earnings"
    TECHNOLOGY = "technology"
    CREDIT_STRESS = "credit_stress"
    BANKING = "banking"
    ENERGY = "energy"
    COMMODITY_SHOCK = "commodity_shock"
    FISCAL_POLICY = "fiscal_policy"
    REGULATION = "regulation"
    MARKET_STRUCTURE = "market_structure"
    SECTOR_ROTATION = "sector_rotation"
    OTHER = "other"


class HistoricalSource(RegimeModel):
    reference: str
    headline: str = Field(max_length=350)
    publisher: str = Field(max_length=150)
    published_at: datetime
    url: str
    summary: str | None = Field(default=None, max_length=650)
    source_classification: Literal[
        "official_policy", "official_data", "wire", "financial_news", "aggregation"
    ]
    entities: tuple[str, ...] = ()
    category: EventCategory
    # Reviewed metadata summaries only; unreviewed RSS headlines are never narrated as facts.
    reviewed_summary: bool = False
    publication_precision: Literal["timestamp", "date"] = "timestamp"
    mechanism_scenario: Literal[
        "weak_spending", "growth_expansion", "energy_inflation", "technology_expectations"
    ] | None = None

    @field_validator("published_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Publication timestamp needs a timezone")
        return value

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        from urllib.parse import urlsplit
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("Source must have a public HTTPS URL without credentials")
        return value


class CaseStatement(RegimeModel):
    kind: Literal["FACT", "INTERPRETATION", "HISTORICAL OUTCOME", "COMPARISON", "LESSON"]
    text: str
    evidence_refs: tuple[str, ...] = ()


class MarketMechanism(RegimeModel):
    kind: Literal["INTERPRETATION"] = "INTERPRETATION"
    category: EventCategory
    steps: tuple[str, ...]
    explanation: str
    evidence_refs: tuple[str, ...]
    certainty: Literal["possible"] = "possible"
    scenario: str | None = None


class HistoricalEvent(RegimeModel):
    event_date: date
    category: EventCategory
    source_ref: str
    affected_markets: tuple[str, ...]
    evidence: CaseStatement
    known_by_analogue_date: Literal[True] = True
    possible_market_mechanism: MarketMechanism | None = None


class EventCluster(RegimeModel):
    category: EventCategory
    title: str
    facts: tuple[CaseStatement, ...]
    evidence_refs: tuple[str, ...]
    interpretation: MarketMechanism | None = None


class HistoricalMarketContext(RegimeModel):
    analogue_date: date
    market: Literal["US"] = "US"
    evidence_version: str = "historical-context-v1"
    window_days: int = Field(default=7, ge=0, le=31)
    main_events: tuple[HistoricalEvent, ...] = ()
    event_clusters: tuple[EventCluster, ...] = ()
    evidence_refs: tuple[HistoricalSource, ...] = ()
    retrieval_status: str = "metadata unavailable"
    evidence_quality: str = "metadata only"
    evidence_completeness: str = "partial"
    source_count: int = 0

    @model_validator(mode="after")
    def enforce_window_and_references(self):
        start = datetime.combine(self.analogue_date - timedelta(days=self.window_days),
                                 time.min, timezone.utc)
        end = datetime.combine(self.analogue_date, time.max, timezone.utc)
        refs = {s.reference: s for s in self.evidence_refs}
        if len(refs) != len(self.evidence_refs) or self.source_count != len(refs):
            raise ValueError("Invalid source inventory")
        if any(not start <= s.published_at <= end for s in refs.values()):
            raise ValueError("Evidence outside historical publication window")
        for event in self.main_events:
            if event.source_ref not in refs or event.event_date > self.analogue_date:
                raise ValueError("Invalid historical event")
        used = [r for c in self.event_clusters for r in c.evidence_refs]
        used += [r for c in self.event_clusters for f in c.facts for r in f.evidence_refs]
        used += [r for c in self.event_clusters if c.interpretation
                 for r in c.interpretation.evidence_refs]
        used += [r for e in self.main_events for r in e.evidence.evidence_refs]
        used += [r for e in self.main_events if e.possible_market_mechanism
                 for r in e.possible_market_mechanism.evidence_refs]
        if any(r not in refs for r in used):
            raise ValueError("Unresolved historical evidence reference")
        return self


class CaseOutcome(RegimeModel):
    kind: Literal["HISTORICAL OUTCOME"] = "HISTORICAL OUTCOME"
    trading_days: Literal[20, 60]
    start_date: date
    end_date: date | None
    asset_return: float | None = None
    benchmark_return: float | None = None
    benchmark_excess_return: float | None = None
    maximum_drawdown: float | None = None
    benchmark_maximum_drawdown: float | None = None
    sufficient_asset_data: bool = False
    sufficient_benchmark_data: bool = False


class HistoricalMarketCase(RegimeModel):
    analogue_date: date
    current_as_of: date
    similarity_score: float
    ticker: str
    benchmark: str
    benchmark_label: str
    mode: Literal["asset_specific", "market_regime_only"]
    scope_notice: str
    regime: dict[str, str]
    context_before_date: HistoricalMarketContext
    macro_conditions: tuple[CaseStatement, ...] = ()
    market_mechanisms: tuple[MarketMechanism, ...] = ()
    historical_outcomes: dict[str, CaseOutcome]
    similarities_to_today: tuple[CaseStatement, ...] = ()
    internal_differences_from_today: tuple[CaseStatement, ...] = ()
    lessons: tuple[CaseStatement, ...] = ()
    evidence_refs: tuple[HistoricalSource, ...] = ()
    notice: str = "This is historical context, not a forecast."
    current_evidence_refs: tuple[HistoricalSource, ...] = ()

    @model_validator(mode="after")
    def case_chronology(self):
        if (self.context_before_date.analogue_date != self.analogue_date
                or self.analogue_date >= self.current_as_of):
            raise ValueError("Case and historical context cutoffs must agree")
        if self.evidence_refs != self.context_before_date.evidence_refs:
            raise ValueError("Historical source inventories must agree")
        for key, outcome in self.historical_outcomes.items():
            if key != f"{outcome.trading_days}d" or outcome.start_date != self.analogue_date:
                raise ValueError("Outcome window does not match the case")
            if outcome.end_date and not self.analogue_date < outcome.end_date <= self.current_as_of:
                raise ValueError("Outcome extends beyond current cutoff")
            if self.mode == "market_regime_only" and (outcome.asset_return is not None
                    or outcome.benchmark_excess_return is not None or outcome.sufficient_asset_data):
                raise ValueError("Market-only case cannot contain company outcomes")
        if any(not self.current_as_of - timedelta(days=7) <=
               s.published_at.astimezone(timezone.utc).date() <= self.current_as_of
               for s in self.current_evidence_refs):
            raise ValueError("Current source outside comparison window")
        return self

    def beginner_view(self):
        """Presentation only; reasoning, audit data and unavailable 60-day windows stay internal."""
        def comparison_text(text):
            for prefix in ("Broad equities", "Treasury yields", "Investors", "Market price swings"):
                text = text.replace(f"{prefix} are ", f"{prefix} were ")
            return text.replace(" in both snapshots.", " in both periods.")

        return {
            "analogue_date": str(self.analogue_date), "current_as_of": str(self.current_as_of),
            "display_date": self.analogue_date.strftime("%d %B %Y"),
            "similarity_score": self.similarity_score, "ticker": self.ticker,
            "benchmark_label": self.benchmark_label, "mode": self.mode,
            "scope_notice": self.scope_notice,
            "developments": [{"title": c.title,
                              "facts": [f.text for f in c.facts],
                              "evidence_refs": list(c.evidence_refs)}
                             for c in self.context_before_date.event_clusters],
            "macro_conditions": [s.text for s in self.macro_conditions],
            "outcomes": [{key: value for key, value in o.model_dump(mode="json").items()
                          if key in {"trading_days", "start_date", "end_date", "benchmark_return",
                                     "benchmark_maximum_drawdown", "sufficient_benchmark_data"}}
                         for o in sorted(self.historical_outcomes.values(),
                                         key=lambda outcome: outcome.trading_days)
                         if o.trading_days == 20 or o.sufficient_benchmark_data],
            "similarities": [comparison_text(s.text) for s in self.similarities_to_today],
            "sources": [{"reference": s.reference, "headline": s.headline,
                         "publisher": s.publisher, "published_at": s.published_at.isoformat(),
                         "publication_precision": s.publication_precision,
                         "url": s.url} for s in (*self.evidence_refs, *self.current_evidence_refs)],
            "notice": self.notice,
        }
