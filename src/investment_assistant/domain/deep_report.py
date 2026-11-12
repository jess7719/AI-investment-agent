"""Compact validated structures for decision-useful deep research reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investment_assistant.domain.evidence import EvidenceDepth
from investment_assistant.domain.research import REFERENCE_PATTERN, TokenUsage

ShortText = Annotated[str, Field(min_length=1, max_length=500)]
VeryShortText = Annotated[str, Field(min_length=1, max_length=700)]
REFERENCE_MARKER = re.compile(r"\[(?:Market Data|News \d+|Filing \d+)\]")


class Stance(StrEnum):
    BULLISH = "bullish"
    CAUTIOUSLY_BULLISH = "cautiously_bullish"
    NEUTRAL = "neutral"
    CAUTIOUSLY_BEARISH = "cautiously_bearish"
    BEARISH = "bearish"


class ImpactDirection(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    NEUTRAL = "neutral"


class TimeHorizon(StrEnum):
    NEAR_TERM = "near_term"
    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ThesisStatus(StrEnum):
    STRENGTHENED = "strengthened"
    UNCHANGED = "unchanged"
    WEAKENED = "weakened"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class InvestorActionValue(StrEnum):
    HOLD_AND_MONITOR = "hold_and_monitor"
    WAIT = "wait"
    RESEARCH_FURTHER = "research_further"
    ADD_ONLY_SMALL_STAGED_AMOUNTS = "add_only_small_staged_amounts"
    REVIEW_POSITION_SIZE = "review_position_size"
    RECONSIDER_THESIS = "reconsider_thesis"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Conclusion(StrictModel):
    stance: Stance
    summary: list[VeryShortText] = Field(min_length=1, max_length=5)
    most_important_reason: VeryShortText


class KeyEvidence(StrictModel):
    reference: Annotated[str, Field(min_length=1, max_length=40)]
    fact: ShortText
    why_it_matters: ShortText
    impact_direction: ImpactDirection
    time_horizon: TimeHorizon
    confidence: ConfidenceLevel


class ProbabilityRange(StrictModel):
    low_percent: int = Field(ge=0, le=100)
    high_percent: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def ordered(self) -> ProbabilityRange:
        if self.low_percent > self.high_percent:
            raise ValueError("Probability range is reversed.")
        return self


class NearTermScenario(StrictModel):
    summary: VeryShortText
    probability: ProbabilityRange


class NearTermView(StrictModel):
    base_case: NearTermScenario
    bull_case: NearTermScenario
    bear_case: NearTermScenario
    main_driver: VeryShortText
    what_would_change_the_view: VeryShortText


class MediumTermView(StrictModel):
    base_case: VeryShortText
    bull_case: VeryShortText
    bear_case: VeryShortText
    main_catalysts: list[VeryShortText] = Field(max_length=3)
    main_risks: list[VeryShortText] = Field(max_length=3)
    what_would_change_the_view: VeryShortText


class LongTermThesis(StrictModel):
    status: ThesisStatus
    core_thesis: ShortText
    supporting_factors: list[VeryShortText] = Field(max_length=3)
    threats: list[VeryShortText] = Field(max_length=3)
    thesis_invalidation_conditions: list[VeryShortText] = Field(max_length=3)


class InvestorAction(StrictModel):
    action_for_current_position: InvestorActionValue
    whether_to_add_more_now: InvestorActionValue
    evidence_before_adding: list[VeryShortText] = Field(max_length=3)
    reason: ShortText


class ThesisTriggers(StrictModel):
    positive_triggers: list[VeryShortText] = Field(max_length=3)
    negative_triggers: list[VeryShortText] = Field(max_length=3)
    upcoming_events_to_monitor: list[VeryShortText] = Field(max_length=3)


class ConfidenceAssessment(StrictModel):
    level: ConfidenceLevel
    reason: VeryShortText


class Confidence(StrictModel):
    data: ConfidenceAssessment
    catalyst_interpretation: ConfidenceAssessment
    fundamentals: ConfidenceAssessment
    near_term_forecast: ConfidenceAssessment
    long_term_thesis: ConfidenceAssessment


class DeepResearchReport(StrictModel):
    """Schema used for every future ``analyse --deep`` request."""

    conclusion: Conclusion
    key_evidence: list[KeyEvidence] = Field(max_length=5)
    near_term_view: NearTermView
    medium_term_view: MediumTermView
    long_term_thesis: LongTermThesis
    investor_action: InvestorAction
    thesis_triggers: ThesisTriggers
    confidence: Confidence
    limitations: list[VeryShortText] = Field(max_length=5)


@dataclass(frozen=True, slots=True)
class GeneratedDeepResearchReport:
    report: DeepResearchReport
    model: str
    usage: TokenUsage
    response_id: str | None = None
    request_timestamp: datetime | None = None
    raw_output: str | None = None
    response_status: str | None = None
    incomplete_details: object | None = None


def validate_deep_report(
    report: DeepResearchReport,
    *,
    allowed_references: set[str],
    allowed_main_clusters: set[str] | None = None,
    evidence_depth_by_reference: dict[str, EvidenceDepth] | None = None,
) -> None:
    """Reject invented citations while retaining the previous call signature."""

    del allowed_main_clusters, evidence_depth_by_reference
    for item in report.key_evidence:
        if (
            not REFERENCE_PATTERN.fullmatch(item.reference)
            or item.reference not in allowed_references
        ):
            raise ValueError(f"Unsupported evidence reference: {item.reference}")
    for marker in _all_reference_markers(report):
        if marker not in allowed_references:
            raise ValueError(f"Unsupported evidence reference: {marker}")


def _all_reference_markers(report: DeepResearchReport) -> set[str]:
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str):
            found.update(REFERENCE_MARKER.findall(value))
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(report.model_dump(mode="python"))
    return found
