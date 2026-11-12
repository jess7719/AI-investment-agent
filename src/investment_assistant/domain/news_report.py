"""Strict structures for a beginner-friendly, evidence-grounded daily news report."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investment_assistant.domain.evidence import EvidenceDepth
from investment_assistant.domain.research import REFERENCE_PATTERN, TokenUsage

ShortText = Annotated[str, Field(min_length=1, max_length=700)]


class NewsFlow(StrEnum):
    CLEARLY_POSITIVE = "clearly positive"
    SLIGHTLY_POSITIVE = "slightly positive"
    MIXED = "mixed"
    SLIGHTLY_NEGATIVE = "slightly negative"
    CLEARLY_NEGATIVE = "clearly negative"


class FinalStance(StrEnum):
    CLEARLY_POSITIVE = "clearly positive"
    SLIGHTLY_POSITIVE = "slightly positive"
    NEUTRAL = "neutral"
    SLIGHTLY_NEGATIVE = "slightly negative"
    CLEARLY_NEGATIVE = "clearly negative"


class StockEffect(StrEnum):
    STRONGLY_POSITIVE = "strongly positive"
    POSITIVE = "positive"
    SLIGHTLY_POSITIVE = "slightly positive"
    NEUTRAL = "neutral"
    SLIGHTLY_NEGATIVE = "slightly negative"
    NEGATIVE = "negative"
    STRONGLY_NEGATIVE = "strongly negative"


class EffectHorizon(StrEnum):
    IMMEDIATE = "immediate"
    NEXT_FEW_WEEKS = "next few weeks"
    NEXT_FEW_MONTHS = "next few months"
    LONG_TERM = "long term"


class EvidenceStrength(StrEnum):
    STRONG = "strong"
    REASONABLE = "reasonable"
    WEAK = "weak"


class BusinessCaseEffect(StrEnum):
    STRENGTHENS = "strengthens"
    WEAKENS = "weakens"
    UNCHANGED = "unchanged"


class PrimaryEffect(StrEnum):
    FUNDAMENTALS = "fundamentals"
    SHORT_TERM_SENTIMENT = "short-term market sentiment"
    BOTH = "both fundamentals and sentiment"


class PriceReactionAssessment(StrEnum):
    UNDERSTANDABLE = "broadly understandable"
    EXAGGERATED = "exaggerated"
    UNSUPPORTED = "unsupported by the evidence"
    UNCLEAR = "unclear"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CitedPoint(StrictModel):
    text: ShortText
    references: list[str] = Field(min_length=1, max_length=4)


class PlainEnglishSummary(StrictModel):
    sentences: list[CitedPoint] = Field(min_length=1, max_length=5)
    overall_news_flow: NewsFlow


class ImportantNewsEvent(StrictModel):
    cluster_id: Annotated[str, Field(min_length=1, max_length=80)]
    title: Annotated[str, Field(min_length=1, max_length=140)]
    what_happened: CitedPoint
    key_facts: list[CitedPoint] = Field(min_length=1, max_length=5)
    why_it_matters: list[CitedPoint] = Field(min_length=1, max_length=4)
    likely_stock_effect: StockEffect
    time_horizon: EffectHorizon
    evidence_strength: EvidenceStrength
    evidence_type: EvidenceDepth
    evidence_reason: Annotated[str, Field(min_length=1, max_length=350)]
    beginner_takeaway: Annotated[str, Field(min_length=1, max_length=500)]


class NoiseItem(StrictModel):
    title_or_theme: Annotated[str, Field(min_length=1, max_length=180)]
    references: list[str] = Field(min_length=1, max_length=3)
    why_low_value: Annotated[str, Field(min_length=1, max_length=400)]


class OverallEffect(StrictModel):
    business_case_effect: BusinessCaseEffect
    primary_effect: PrimaryEffect
    price_reaction_assessment: PriceReactionAssessment
    most_plausible_market_interpretation: CitedPoint
    explanation: list[CitedPoint] = Field(min_length=1, max_length=4)
    final_stance: FinalStance


class InvestorActionFramework(StrictModel):
    profile_basis: Annotated[str, Field(min_length=1, max_length=300)]
    if_already_owns: list[ShortText] = Field(min_length=1, max_length=4)
    if_does_not_own: list[ShortText] = Field(min_length=1, max_length=4)
    specific_action_trigger: ShortText


class WatchItem(StrictModel):
    event_or_data: ShortText
    timeframe: Annotated[str, Field(min_length=1, max_length=160)]
    positive_result_means: ShortText
    negative_result_means: ShortText
    references: list[str] = Field(min_length=1, max_length=4)


class DailyNewsReport(StrictModel):
    todays_news_in_plain_english: PlainEnglishSummary
    important_events: list[ImportantNewsEvent] = Field(min_length=1, max_length=3)
    noise_to_ignore: list[NoiseItem] = Field(max_length=3)
    overall_effect: OverallEffect
    investor_action: InvestorActionFramework
    what_to_watch_next: list[WatchItem] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def distinct_event_clusters(self) -> DailyNewsReport:
        cluster_ids = [event.cluster_id for event in self.important_events]
        if len(cluster_ids) != len(set(cluster_ids)):
            raise ValueError("Important events must use distinct event clusters.")
        return self


@dataclass(frozen=True, slots=True)
class GeneratedDailyNewsReport:
    report: DailyNewsReport
    model: str
    usage: TokenUsage


def validate_daily_news_report(
    report: DailyNewsReport,
    *,
    allowed_references: set[str],
    allowed_main_clusters: set[str],
    evidence_depth_by_reference: dict[str, EvidenceDepth],
) -> None:
    """Reject invented references, events, and overstated evidence depth."""

    for event in report.important_events:
        if event.cluster_id not in allowed_main_clusters:
            raise ValueError(f"Unsupported main event cluster: {event.cluster_id}")
        event_references = _event_references(event)
        _validate_references(event_references, allowed_references)
        supplied_depths = {
            evidence_depth_by_reference[reference]
            for reference in event_references
            if reference in evidence_depth_by_reference
        }
        if event.evidence_type not in supplied_depths:
            raise ValueError(
                f"Event {event.cluster_id} claimed an unsupported evidence type."
            )
    for point in report.todays_news_in_plain_english.sentences:
        _validate_references(point.references, allowed_references)
    for item in report.noise_to_ignore:
        _validate_references(item.references, allowed_references)
    _validate_references(
        report.overall_effect.most_plausible_market_interpretation.references,
        allowed_references,
    )
    for point in report.overall_effect.explanation:
        _validate_references(point.references, allowed_references)
    for item in report.what_to_watch_next:
        _validate_references(item.references, allowed_references)


def _event_references(event: ImportantNewsEvent) -> list[str]:
    references = [*event.what_happened.references]
    for point in [*event.key_facts, *event.why_it_matters]:
        references.extend(point.references)
    return list(dict.fromkeys(references))


def _validate_references(references: list[str], allowed_references: set[str]) -> None:
    for reference in references:
        if not REFERENCE_PATTERN.fullmatch(reference) or reference not in allowed_references:
            raise ValueError(f"Unsupported evidence reference: {reference}")
