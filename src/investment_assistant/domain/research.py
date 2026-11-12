"""Validated structures for evidence-grounded AI research reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

ShortText = Annotated[str, Field(min_length=1, max_length=700)]
REFERENCE_PATTERN = re.compile(r"^\[(?:Market Data|News \d+|Filing \d+)\]$")


class EvidenceBasis(StrEnum):
    MARKET_DATA = "market data"
    HEADLINE_AND_METADATA = "headline and metadata"
    FILING_METADATA = "filing metadata"
    STRUCTURED_FORM4_METADATA = "structured Form 4 metadata"
    FULLER_EVIDENCE = "fuller evidence"


class ThesisStatus(StrEnum):
    STRENGTHENED = "strengthened"
    UNCHANGED = "unchanged"
    WEAKENED = "weakened"
    INSUFFICIENT_EVIDENCE = "insufficient evidence"


class InvestorResponse(StrEnum):
    MONITOR = "monitor"
    RESEARCH_FURTHER = "research further"
    WAIT_FOR_MORE_EVIDENCE = "wait for more evidence"
    REVIEW_POSITION_SIZE = "review position size"
    REVIEW_ORIGINAL_THESIS = "review original thesis"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceStatement(StrictModel):
    statement: ShortText
    references: list[str] = Field(min_length=1, max_length=4)
    evidence_basis: EvidenceBasis
    uncertainty: Annotated[str, Field(min_length=1, max_length=350)]


class ImportantInformation(StrictModel):
    reference: str
    source: Annotated[str, Field(min_length=1, max_length=150)]
    classification: Annotated[str, Field(min_length=1, max_length=80)]
    why_it_may_matter: ShortText
    evidence_basis: EvidenceBasis
    uncertainty: Annotated[str, Field(min_length=1, max_length=350)]


class ResearchReport(StrictModel):
    executive_summary: list[EvidenceStatement] = Field(min_length=1, max_length=4)
    verified_facts: list[EvidenceStatement] = Field(min_length=1, max_length=12)
    market_movement: list[EvidenceStatement] = Field(min_length=1, max_length=5)
    important_information: list[ImportantInformation] = Field(min_length=1, max_length=8)
    possible_short_term_implications: list[EvidenceStatement] = Field(
        min_length=1, max_length=6
    )
    possible_medium_and_long_term_implications: list[EvidenceStatement] = Field(
        min_length=1, max_length=6
    )
    bull_case: list[EvidenceStatement] = Field(min_length=1, max_length=5)
    bear_case: list[EvidenceStatement] = Field(min_length=1, max_length=5)
    key_risks: list[EvidenceStatement] = Field(min_length=1, max_length=8)
    upcoming_factors_to_monitor: list[EvidenceStatement] = Field(min_length=1, max_length=8)
    evidence_gaps: list[ShortText] = Field(min_length=1, max_length=10)
    investment_thesis_status: ThesisStatus
    suggested_investor_responses: list[InvestorResponse] = Field(min_length=1, max_length=3)
    confidence_level: ConfidenceLevel
    confidence_rationale: Annotated[str, Field(min_length=1, max_length=500)]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class GeneratedResearchReport:
    report: ResearchReport
    model: str
    usage: TokenUsage


def validate_report_evidence(report: ResearchReport, allowed_references: set[str]) -> None:
    """Reject unsupported references and claims of evidence that was never supplied."""

    statements = (
        report.executive_summary
        + report.verified_facts
        + report.market_movement
        + report.possible_short_term_implications
        + report.possible_medium_and_long_term_implications
        + report.bull_case
        + report.bear_case
        + report.key_risks
        + report.upcoming_factors_to_monitor
    )
    for statement in statements:
        _validate_references(statement.references, allowed_references)
        if statement.evidence_basis is EvidenceBasis.FULLER_EVIDENCE:
            raise ValueError("The report claimed fuller evidence that was not supplied.")
    for item in report.important_information:
        _validate_references([item.reference], allowed_references)
        if item.evidence_basis is EvidenceBasis.FULLER_EVIDENCE:
            raise ValueError("The report claimed fuller evidence that was not supplied.")


def _validate_references(references: list[str], allowed_references: set[str]) -> None:
    for reference in references:
        if not REFERENCE_PATTERN.fullmatch(reference) or reference not in allowed_references:
            raise ValueError(f"The report used an unsupported evidence reference: {reference}")
