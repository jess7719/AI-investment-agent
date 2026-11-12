"""Historical comparisons; outcomes are deliberately separate from feature vectors."""

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from investment_assistant.domain.regime import MarketRegimeSnapshot, RegimeEvidence, RegimeModel


class HistoricalRegimeSnapshot(RegimeModel):
    as_of: date
    asset: str | None
    regime: MarketRegimeSnapshot
    features: dict[str, float | None]
    feature_evidence: dict[str, RegimeEvidence]

    def training_features(self) -> dict:
        return {"date": self.as_of.isoformat(), "asset": self.asset, **self.features}

    @model_validator(mode="after")
    def historical_cutoff(self):
        if self.regime.as_of != self.as_of:
            raise ValueError("Feature and regime dates must agree")
        for evidence in self.feature_evidence.values():
            if evidence.timestamp and evidence.timestamp > self.as_of:
                raise ValueError("Future feature observation")
            if any(o.date > self.as_of or o.available_on > self.as_of
                   for o in evidence.observations):
                raise ValueError("Feature observation unavailable at historical cutoff")
        return self


class HistoricalForwardOutcome(RegimeModel):
    trading_days: int
    start: date
    end: date | None
    raw_return: float | None = None
    benchmark_return: float | None = None
    excess_return: float | None = None
    maximum_drawdown: float | None = None
    benchmark_maximum_drawdown: float | None = None
    asset_status: str
    benchmark_status: str
    evidence: tuple[RegimeEvidence, ...] = ()
    label: str = "historical forward outcomes"


class HistoricalAnalogue(RegimeModel):
    date: date
    similarity: float = Field(ge=0, le=100)
    feature_distance: float = Field(ge=0)
    category_penalty: float = Field(ge=0)
    feature_coverage: float = Field(ge=0, le=1)
    snapshot: HistoricalRegimeSnapshot
    outcomes: tuple[HistoricalForwardOutcome, ...]

    def training_row(self) -> dict:
        row = self.snapshot.training_features()
        for outcome in self.outcomes:
            row[f"future_{outcome.trading_days}d_return"] = outcome.raw_return
            row[f"benchmark_future_{outcome.trading_days}d_return"] = outcome.benchmark_return
        return row


class OutcomeSummary(RegimeModel):
    trading_days: int
    sample_size: int
    warning: str
    median_forward_return: float | None
    mean_forward_return: float | None
    positive_return_frequency: float | None
    median_excess_return: float | None
    worst_historical_outcome: float | None
    best_historical_outcome: float | None
    benchmark_sample_size: int
    median_benchmark_return: float | None
    benchmark_warning: str


class AnalogueSearchResult(RegimeModel):
    as_of: date
    ticker: str
    benchmark: str
    mode: Literal["asset_specific", "market_regime_only"]
    asset_specific_status: str
    current: HistoricalRegimeSnapshot
    matches: tuple[HistoricalAnalogue, ...]
    summaries: tuple[OutcomeSummary, ...]
    sample_size: int
    sample_warning: str
    candidate_count: int
    weights: dict[str, float]
    scales: dict[str, float]
    excluded_features: dict[str, str]
    configuration: dict
    notice: str = (
        "These are historical comparisons, not forecasts. Correlation is not causation. "
        "Historical similarity does not imply the same future result. "
        "Forward outcomes are not guaranteed future returns."
    )
    evidence_kind: str = "deterministic contextual evidence"
    engine_version: str = "deterministic-v1"

    @model_validator(mode="after")
    def search_cutoff(self):
        if self.current.as_of != self.as_of or self.sample_size != len(self.matches):
            raise ValueError("Search dates and sample size must agree")
        for match in self.matches:
            if match.date >= self.as_of or match.snapshot.as_of != match.date:
                raise ValueError("Invalid historical analogue date")
            for outcome in match.outcomes:
                if outcome.start != match.date or (outcome.end and not
                        match.date < outcome.end <= self.as_of):
                    raise ValueError("Invalid historical outcome window")
                if any(o.date > self.as_of or o.available_on > self.as_of
                       for e in outcome.evidence for o in e.observations):
                    raise ValueError("Future outcome unavailable at search cutoff")
        return self
