"""Provider-neutral daily facts and auditable deterministic classifications."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Confidence = Literal["high", "medium", "low", "unknown"]


class RegimeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class DailyObservation(RegimeModel):
    date: date
    available_on: date
    value: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)
    source: str
    series: str
    retrieved_at: datetime
    unit: str = "price"

    @model_validator(mode="after")
    def availability(self):
        if self.available_on < self.date:
            raise ValueError("Observation cannot be available before its measurement date")
        if self.retrieved_at.tzinfo is None:
            raise ValueError("Retrieval timestamp must include a timezone")
        return self


class MacroObservation(RegimeModel):
    series: str
    source: str
    observation_date: date
    published_on: date
    retrieved_at: datetime
    value: float
    unit: str
    vintage: str
    decision_grade: bool = False


class RegimeEvidence(RegimeModel):
    reference: str
    source: str
    series: str
    timestamp: date | None = None
    retrieved_at: datetime | None = None
    observed_value: float | None = None
    value: float | None = None
    unit: str
    calculation: str
    rule: str
    confidence: Confidence = "unknown"
    status: Literal["available", "missing", "stale", "partial"]
    # Full input window makes calculations reproducible without re-fetching a provider.
    observations: tuple[DailyObservation, ...] = ()
    macro_observations: tuple[MacroObservation, ...] = ()


class RegimeDimension(RegimeModel):
    classification: str
    confidence: Confidence
    evidence: tuple[RegimeEvidence, ...]
    rule: str
    component_scores: dict[str, float] = Field(default_factory=dict)


CLASSIFICATIONS = {
    "equity_trend": {"strong_uptrend", "uptrend", "sideways", "downtrend", "strong_downtrend"},
    "volatility": {"low", "normal", "elevated", "stressed"},
    "rates": {"falling", "stable", "rising"},
    "monetary_policy": {"tightening", "restrictive_stable", "easing", "supportive_stable"},
    "inflation": {"accelerating", "stable", "cooling"},
    "growth": {"accelerating", "stable", "slowing", "contraction"},
    "risk_sentiment": {"risk_on", "neutral", "risk_off"},
}


class MarketRegimeSnapshot(RegimeModel):
    as_of: date
    benchmark: str
    equity_trend: RegimeDimension
    volatility: RegimeDimension
    rates: RegimeDimension
    monetary_policy: RegimeDimension
    inflation: RegimeDimension
    growth: RegimeDimension
    risk_sentiment: RegimeDimension
    configuration: dict = Field(default_factory=dict)
    engine_version: str = "deterministic-v1"
    evidence_kind: str = "deterministic contextual evidence"

    @property
    def classification(self) -> dict[str, str]:
        return {key: getattr(self, key).classification for key in CLASSIFICATIONS}

    @model_validator(mode="after")
    def validate_dimensions(self):
        for key, allowed in CLASSIFICATIONS.items():
            dimension = getattr(self, key)
            if dimension.classification not in allowed | {"unknown"}:
                raise ValueError(f"Invalid {key} classification")
            if dimension.classification == "unknown" and dimension.confidence != "unknown":
                raise ValueError("Unknown classification must have unknown confidence")
            for evidence in dimension.evidence:
                if evidence.timestamp and evidence.timestamp > self.as_of:
                    raise ValueError("Future evidence in regime snapshot")
                if any(o.date > self.as_of or o.available_on > self.as_of
                       for o in evidence.observations):
                    raise ValueError("Future input in regime snapshot")
                if any(o.published_on > self.as_of or o.observation_date > self.as_of
                       for o in evidence.macro_observations):
                    raise ValueError("Future macro publication in regime snapshot")
        return self
