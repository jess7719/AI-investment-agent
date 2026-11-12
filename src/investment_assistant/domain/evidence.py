"""Evidence depth, extracted content, manual notes, and event clusters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from investment_assistant.domain.news import NewsArticle


class EvidenceDepth(StrEnum):
    OFFICIAL_FILING_EXTRACT = "official filing extract"
    OFFICIAL_COMPANY_RELEASE = "official company release"
    FULL_PUBLIC_ARTICLE_EXTRACT = "full public article extract"
    SOURCE_PROVIDED_SUMMARY = "source-provided summary"
    PUBLIC_METADATA_DESCRIPTION = "public metadata description"
    USER_PROVIDED_NOTES = "user-provided notes"
    HEADLINE_ONLY = "headline only"


EVIDENCE_DEPTH_SCORES = {
    EvidenceDepth.OFFICIAL_FILING_EXTRACT: 100,
    EvidenceDepth.OFFICIAL_COMPANY_RELEASE: 95,
    EvidenceDepth.FULL_PUBLIC_ARTICLE_EXTRACT: 90,
    EvidenceDepth.USER_PROVIDED_NOTES: 80,
    EvidenceDepth.SOURCE_PROVIDED_SUMMARY: 70,
    EvidenceDepth.PUBLIC_METADATA_DESCRIPTION: 55,
    EvidenceDepth.HEADLINE_ONLY: 20,
}


@dataclass(frozen=True, slots=True)
class ExtractedEvidence:
    article: NewsArticle
    evidence_depth: EvidenceDepth
    content: str | None
    original_url: str
    official_url: str | None = None
    filing_sections: tuple[tuple[str, str], ...] = ()
    exhibit_descriptions: tuple[str, ...] = ()
    material_event_summary: str | None = None
    structured_ownership: tuple[dict[str, str | None], ...] = ()
    cache_hit: bool = False
    access_note: str | None = None

    @property
    def extracted_characters(self) -> int:
        return len(self.content or "")

    @property
    def depth_score(self) -> int:
        return EVIDENCE_DEPTH_SCORES[self.evidence_depth]


@dataclass(frozen=True, slots=True)
class ManualNote:
    ticker: str
    publisher: str
    headline: str
    url: str
    publication_date: date
    summary: str
    key_facts: tuple[str, ...]
    why_it_matters: str
    date_added: datetime


@dataclass(slots=True)
class EventCluster:
    cluster_id: str
    label: str
    items: list[ExtractedEvidence] = field(default_factory=list)
    selected: ExtractedEvidence | None = None
    selection_score: float = 0.0


@dataclass(frozen=True, slots=True)
class EvidenceSelection:
    main_clusters: tuple[EventCluster, ...]
    secondary_items: tuple[ExtractedEvidence, ...]
    noise_items: tuple[ExtractedEvidence, ...] = ()
