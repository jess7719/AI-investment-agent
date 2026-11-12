"""Strict scenario contracts: dated facts, conditional reasoning and reproducible evidence state."""

import re
from datetime import date, datetime
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, field_validator, model_validator

from investment_assistant.domain.historical_context import HistoricalMarketCase
from investment_assistant.domain.regime import MarketRegimeSnapshot, RegimeModel

Scope = Literal["market", "company"]
Direction = Literal["positive", "negative", "neutral", "unknown"]
Horizon = Literal["20d", "60d"]
ScenarioName = Literal["base", "bull", "bear"]
FORBIDDEN_PROSE = re.compile(
    r"\b(?:buy|sell|hold|add|reduce)\b|%|\b(?:will rise|will fall|expected return|target price|"
    r"price target|probability|probabilities|risk tolerance|position size|your income|your age)\b",
    re.I,
)
SECRET = re.compile(r"sk-[A-Za-z0-9_-]{12,}|(?:api[_ -]?key|authorization)\s*[:=]", re.I)


def safe_text(value):
    if SECRET.search(value):
        raise ValueError("Secret-like content is not permitted in scenario artifacts")
    return value


def aware(value):
    if value.tzinfo is None:
        raise ValueError("Scenario evidence timestamps require a timezone")
    return value


class ScenarioAsset(RegimeModel):
    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")
    name: str = Field(min_length=1, max_length=150)
    asset_type: str = "equity"


class ScenarioEvidenceRef(RegimeModel):
    reference: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,180}$")
    source: str = Field(min_length=1, max_length=200)
    timestamp: datetime
    available_at: datetime
    evidence_type: Literal[
        "market_data", "market_regime", "current_event", "fundamental", "filing",
        "historical_case", "historical_outcome"
    ]
    asset: str
    asset_relevance: Literal["direct", "market_context", "sector_context"]
    scope: Scope
    temporal: Literal["current", "historical"] = "current"
    decision_grade: bool
    still_relevant: bool = True
    summary: str = Field(min_length=1, max_length=700)
    url: str | None = None
    category: str = "other"
    metric: str | None = None
    value: float | None = None
    unit: str | None = None
    classification: str | None = None
    direction: Direction = "unknown"
    known_catalyst: bool = False
    scheduled_for: datetime | None = None
    source_classification: str = "unclassified"
    evidence_depth: str = "structured observation"
    artifact_id: str | None = None
    reporting_period: str | None = None
    calculation_method: str | None = None
    currency: str | None = None

    _times = field_validator("timestamp", "available_at")(aware)
    _text = field_validator("source", "summary")(safe_text)

    @field_validator("scheduled_for")
    @classmethod
    def schedule_time(cls, value):
        return aware(value) if value else None

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        if value is None:
            return value
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or any(k.lower() in {"api_key", "apikey", "token", "key", "authorization"}
                       for k, _ in parse_qsl(parts.query))):
            raise ValueError("Source links must be public HTTPS URLs without credentials")
        return safe_text(value)

    @model_validator(mode="after")
    def metadata_consistency(self):
        if self.timestamp > self.available_at:
            raise ValueError("Evidence cannot be available before its timestamp")
        if self.scope == "company" and self.asset_relevance != "direct":
            raise ValueError("Company evidence must identify direct asset relevance")
        if self.known_catalyst and (self.scheduled_for is None or
                                  self.still_relevant and not self.decision_grade):
            raise ValueError("Known catalysts need decision-grade dated scheduling evidence")
        return self


class ScenarioEventCluster(RegimeModel):
    cluster_id: str
    category: str
    factual_summary: str
    asset_relevance: str
    market_relevance: bool
    scope: Scope
    evidence_refs: tuple[str, ...]
    timestamp: datetime
    current: bool
    still_relevant: bool


class ScenarioEvidencePackage(RegimeModel):
    asset: ScenarioAsset
    evidence_cutoff: datetime
    current_market_data: tuple[ScenarioEvidenceRef, ...] = ()
    current_events: tuple[ScenarioEvidenceRef, ...] = ()
    market_regime: MarketRegimeSnapshot | None = None
    regime_evidence: tuple[ScenarioEvidenceRef, ...] = ()
    historical_cases: tuple[HistoricalMarketCase, ...] = ()
    historical_evidence: tuple[ScenarioEvidenceRef, ...] = ()
    fundamentals: tuple[ScenarioEvidenceRef, ...] = ()
    filings: tuple[ScenarioEvidenceRef, ...] = ()
    event_clusters: tuple[ScenarioEventCluster, ...] = ()
    regime_snapshot_id: str | None = None
    historical_case_ids: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    source_artifacts: tuple[str, ...] = ()
    schema_version: str = "scenario-evidence-v1"

    _cutoff = field_validator("evidence_cutoff")(aware)

    def current_refs(self):
        return (*self.current_market_data, *self.current_events, *self.regime_evidence,
                *self.fundamentals, *self.filings)

    @model_validator(mode="after")
    def cutoff_and_references(self):
        safe_text(self.model_dump_json())
        refs = (*self.current_refs(), *self.historical_evidence)
        index = {r.reference: r for r in refs}
        if len(index) != len(refs):
            raise ValueError("Duplicate evidence reference")
        for record in refs:
            if record.timestamp > self.evidence_cutoff or record.available_at > self.evidence_cutoff:
                raise ValueError("Future evidence is forbidden")
            if record.scope == "company" and record.asset != self.asset.ticker:
                raise ValueError("Company evidence belongs to a different asset")
        for field, kind in ((self.current_market_data, "market_data"),
                            (self.current_events, "current_event"),
                            (self.regime_evidence, "market_regime"),
                            (self.fundamentals, "fundamental"), (self.filings, "filing")):
            if any(r.evidence_type != kind or r.temporal != "current" for r in field):
                raise ValueError("Evidence in the wrong package section")
        if any(r.temporal != "historical" or r.evidence_type not in
               {"historical_case", "historical_outcome"} or r.scope != "market"
               for r in self.historical_evidence):
            raise ValueError("Historical benchmark references must be historical market evidence")
        if self.market_regime and self.market_regime.as_of > self.evidence_cutoff.date():
            raise ValueError("Future market regime")
        if self.regime_evidence and not self.market_regime:
            raise ValueError("Regime evidence requires a regime snapshot")
        for record in self.regime_evidence:
            if (record.scope != "market" or record.metric not in self.market_regime.classification or
                    record.classification != self.market_regime.classification[record.metric]):
                raise ValueError("Regime evidence disagrees with its underlying snapshot")
        for cluster in self.event_clusters:
            if not cluster.evidence_refs or any(r not in index for r in cluster.evidence_refs):
                raise ValueError("Event cluster has unresolved evidence references")
            members = [index[r] for r in cluster.evidence_refs]
            if any(r.scope != cluster.scope or r.evidence_type not in {"current_event", "filing"}
                   for r in members):
                raise ValueError("Event cluster mixes evidence scopes or types")
            if cluster.timestamp > self.evidence_cutoff:
                raise ValueError("Future event cluster")
            if any(r.summary not in cluster.factual_summary for r in members):
                raise ValueError("Event cluster summary must preserve its supplied factual summaries")
        case_ids = tuple(f"{c.analogue_date}/{c.benchmark}/{c.ticker}" for c in self.historical_cases)
        if len(set(case_ids)) != len(case_ids) or case_ids != self.historical_case_ids:
            raise ValueError("Historical case IDs must be unique and agree with cases")
        for case in self.historical_cases:
            if case.ticker != self.asset.ticker or case.current_as_of > self.evidence_cutoff.date():
                raise ValueError("Historical comparison belongs to another asset or a future cutoff")
            for outcome in case.historical_outcomes.values():
                if outcome.end_date and outcome.end_date > self.evidence_cutoff.date():
                    raise ValueError("Historical outcome was not available by the evidence cutoff")
        return self


class ScenarioDriver(RegimeModel):
    driver_id: str
    statement: str
    factor: Literal["regime", "momentum", "events", "fundamentals", "historical"]
    scope: Scope
    direction: Direction
    role: Literal["supports", "restrains", "context"]
    kind: Literal["current_observation", "known_catalyst", "historical_context"]
    evidence_refs: tuple[str, ...]
    internal_weight: float = Field(ge=0, le=1)
    possible_mechanism: tuple[str, ...] = ()
    mechanism_status: Literal["possible"] = "possible"


class ScenarioTrigger(RegimeModel):
    text: str
    kind: Literal["conditional_future_development", "known_catalyst"]
    scope: Scope
    evidence_refs: tuple[str, ...] = ()


class Scenario(RegimeModel):
    name: ScenarioName
    horizon: Horizon
    summary: str
    orientation: Literal[
        "cautious", "balanced", "constructive", "insufficient_evidence",
        "conditional_upside", "conditional_downside"
    ]
    drivers: tuple[ScenarioDriver, ...]
    supporting_evidence: tuple[str, ...]
    historical_context: tuple[str, ...]
    conditions: tuple[ScenarioTrigger, ...] = Field(min_length=1)
    invalidation_triggers: tuple[ScenarioTrigger, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def conditional_language(self):
        texts = [self.summary, *(d.statement for d in self.drivers),
                 *(t.text for t in (*self.conditions, *self.invalidation_triggers))]
        if any(FORBIDDEN_PROSE.search(t) or SECRET.search(t) for t in texts):
            raise ValueError("Scenario prose cannot contain actions, numeric forecasts or probabilities")
        if self.name == "bull" and self.orientation != "conditional_upside":
            raise ValueError("Bull scenario must be conditional upside")
        if self.name == "bear" and self.orientation != "conditional_downside":
            raise ValueError("Bear scenario must be conditional downside")
        if not self.summary:
            raise ValueError("Scenario needs a summary")
        return self


class HistoricalScenarioSummary(RegimeModel):
    horizon: Horizon
    scope: Literal["market"] = "market"
    outcome_basis: Literal["benchmark"] = "benchmark"
    benchmark: str | None = None
    sample_size: int
    independent_sample_size: int
    median_return: float | None = None
    positive_outcome_frequency: float | None = None
    worst_outcome: float | None = None
    best_outcome: float | None = None
    outcome_dispersion: float | None = None
    interquartile_range: float | None = None
    historical_support: tuple[str, ...] = ()
    historical_contradiction: tuple[str, ...] = ()
    historical_dispersion: str
    influence: Literal["limited_context", "disabled"]
    reasons: tuple[str, ...]
    case_ids: tuple[str, ...]
    independent_case_ids: tuple[str, ...]


class ScenarioContradiction(RegimeModel):
    positive_refs: tuple[str, ...]
    negative_refs: tuple[str, ...]
    explanation: str


class ScenarioSet(RegimeModel):
    scenario_date: date
    asset: ScenarioAsset
    generated_at: datetime
    evidence_cutoff: datetime
    evidence_state: ScenarioEvidencePackage
    scenarios: tuple[Scenario, ...]
    historical_summaries: dict[Horizon, HistoricalScenarioSummary]
    contradictory_evidence: tuple[ScenarioContradiction, ...]
    internal_priorities: dict[Horizon, dict[str, float]]
    regime_snapshot_id: str | None
    historical_case_ids: tuple[str, ...]
    scenario_version: str = "deterministic-scenarios-v1"
    notice: str = "These are conditional scenarios, not forecasts or personalised investment advice."

    _times = field_validator("generated_at", "evidence_cutoff")(aware)

    @model_validator(mode="after")
    def validate_scenarios(self):
        package = self.evidence_state
        if (self.asset != package.asset or self.evidence_cutoff != package.evidence_cutoff
                or self.scenario_date != self.evidence_cutoff.date()
                or self.regime_snapshot_id != package.regime_snapshot_id
                or self.historical_case_ids != package.historical_case_ids):
            raise ValueError("Scenario identity and evidence state must agree")
        if self.generated_at < self.evidence_cutoff:
            raise ValueError("Scenario evidence cutoff cannot be after generation")
        expected = {(h, n) for h in ("20d", "60d") for n in ("base", "bull", "bear")}
        if len(self.scenarios) != 6 or {(s.horizon, s.name) for s in self.scenarios} != expected:
            raise ValueError("Exactly base/bull/bear for each horizon are required")
        index = {r.reference: r for r in (*package.current_refs(), *package.historical_evidence)}
        for scenario in self.scenarios:
            used = set(scenario.supporting_evidence) | set(scenario.historical_context)
            for driver in scenario.drivers:
                used.update(driver.evidence_refs)
                if not driver.evidence_refs:
                    raise ValueError("Observed drivers require source references")
                for ref in driver.evidence_refs:
                    if ref in index:
                        evidence = index[ref]
                        if (not evidence.decision_grade or not evidence.still_relevant
                                or evidence.scope != driver.scope):
                            raise ValueError("Driver uses ineligible evidence or mismatched scope")
                        if driver.kind == "known_catalyst" and not evidence.known_catalyst:
                            raise ValueError("Invented known catalyst")
                        if evidence.temporal == "historical" and (driver.scope != "market" or
                                driver.kind != "historical_context"):
                            raise ValueError("Historical benchmarks must remain market-level context")
                    elif driver.scope != "market" or driver.kind != "historical_context":
                        raise ValueError("Historical benchmarks must remain market-level context")
            for trigger in (*scenario.conditions, *scenario.invalidation_triggers):
                used.update(trigger.evidence_refs)
                if trigger.kind == "known_catalyst" and not any(
                    ref in index and index[ref].known_catalyst for ref in trigger.evidence_refs
                ):
                    raise ValueError("Known catalyst trigger requires scheduling evidence")
            if not used <= set(index):
                raise ValueError("Scenario contains unresolved evidence references")
        for name in ("base", "bull", "bear"):
            summaries = [s.summary for s in self.scenarios if s.name == name]
            if summaries[0] == summaries[1]:
                raise ValueError("Horizon reasoning must differ")
        return self

    def beginner_view(self):
        def why(scenario):
            supporting = [d for d in scenario.drivers if d.role == "supports"]
            restraining = [d for d in scenario.drivers if d.role == "restrains"]
            chosen = supporting[:2] + restraining[:1]
            chosen += [d for d in scenario.drivers if d not in chosen][:3 - len(chosen)]
            return [("Current constraint: " if d.role == "restrains" else "") + d.statement
                    for d in chosen]

        return {
            "ticker": self.asset.ticker, "evidence_cutoff": self.evidence_cutoff.isoformat(),
            "market_regime_as_of": str(self.evidence_state.market_regime.as_of)
                if self.evidence_state.market_regime else None,
            "generated_at": self.generated_at.isoformat(), "notice": self.notice,
            "limitations": list(self.evidence_state.missing_evidence),
            "horizons": [{"label": f"{h[:-1]} trading days", "horizon": h,
                "scenarios": [{"name": s.name, "label": {"base": "Base case", "bull": "Bull case",
                    "bear": "Bear case"}[s.name], "summary": s.summary,
                    "why": why(s),
                    "conditions": [t.text for t in s.conditions],
                    "invalidation_triggers": [t.text for t in s.invalidation_triggers]}
                    for s in self.scenarios if s.horizon == h]} for h in ("20d", "60d")],
        }
