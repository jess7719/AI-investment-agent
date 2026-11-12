"""Read-only explanations derived from an existing analogue result, never scoring inputs."""

from datetime import date
from typing import Literal

from investment_assistant.domain.regime import Confidence, RegimeModel


class CaseRegimeDimension(RegimeModel):
    name: str
    historical: str
    current: str
    historical_confidence: Confidence
    current_confidence: Confidence


class CaseFeatureComparison(RegimeModel):
    feature: str
    label: str
    historical: float | None
    current: float | None
    weight: float
    scale: float
    normalized_distance: float
    weighted_distance: float
    status: Literal["matching", "mismatching", "unavailable"]
    explanation: str


class CaseForwardOutcome(RegimeModel):
    trading_days: int
    end: date | None
    benchmark_return: float | None
    benchmark_drawdown: float | None
    asset_return: float | None
    excess_return: float | None


class AnalogueCaseCard(RegimeModel):
    historical_date: date
    current_as_of: date
    similarity: float
    benchmark: str
    ticker: str
    mode: str
    historical_regime: tuple[CaseRegimeDimension, ...]
    why_similar: str
    similarities: tuple[str, ...]
    differences: tuple[str, ...]
    matching_features: tuple[CaseFeatureComparison, ...]
    mismatching_features: tuple[CaseFeatureComparison, ...]
    feature_comparisons: tuple[CaseFeatureComparison, ...]
    unavailable_evidence: tuple[str, ...]
    outcomes: tuple[CaseForwardOutcome, ...]
    what_this_case_can_teach_us: str
    why_this_case_may_not_apply_today: tuple[str, ...]
    sample_warning: str
    explanation_version: str = "deterministic-case-v1"
