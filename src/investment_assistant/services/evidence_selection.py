"""Cluster related stories and select diverse, evidence-rich events."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from investment_assistant.domain.evidence import (
    EVIDENCE_DEPTH_SCORES,
    EvidenceDepth,
    EvidenceSelection,
    EventCluster,
    ExtractedEvidence,
)
from investment_assistant.domain.news import ContentClassification

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "spcx",
    "spacex",
    "space",
    "exploration",
    "technologies",
    "corp",
}


def select_evidence(items: list[ExtractedEvidence]) -> EvidenceSelection:
    unique, duplicates = _deduplicate(items)
    eligible = [item for item in unique if _eligible(item)]
    ineligible = [item for item in unique if not _eligible(item)]
    clusters: list[EventCluster] = []
    for item in eligible:
        tokens = _tokens(item)
        cluster = next(
            (
                candidate
                for candidate in clusters
                if _same_event(item, candidate.items[0], tokens)
            ),
            None,
        )
        if cluster is None:
            cluster = EventCluster(
                cluster_id=f"event-{len(clusters) + 1}",
                label=item.article.headline,
            )
            clusters.append(cluster)
        cluster.items.append(item)

    for cluster in clusters:
        ranked = sorted(cluster.items, key=_selection_score, reverse=True)
        cluster.selected = ranked[0]
        cluster.selection_score = _selection_score(ranked[0]) + min(len(cluster.items) - 1, 3) * 2
        cluster.label = ranked[0].article.headline
    clusters.sort(key=lambda cluster: cluster.selection_score, reverse=True)
    main_candidates = [
        cluster
        for cluster in clusters
        if cluster.selected
        and _main_event_eligible(cluster.selected)
    ]
    main = tuple(main_candidates[:3])
    selected_ids = {id(cluster.selected) for cluster in main}
    main_cluster_ids = {id(cluster) for cluster in main}
    demoted_commentary = [
        cluster.selected
        for cluster in clusters
        if id(cluster) not in main_cluster_ids
        and cluster.selected is not None
        and _noise_candidate(cluster.selected)
    ]
    repeated_event_items = [
        item
        for cluster in main
        for item in cluster.items
        if id(item) not in selected_ids
    ]
    secondary = tuple(
        item
        for item in sorted(eligible, key=_selection_score, reverse=True)
        if id(item) not in selected_ids
    )[:5]
    noise = tuple(
        sorted(
            [*demoted_commentary, *repeated_event_items, *duplicates, *ineligible],
            key=_selection_score,
            reverse=True,
        )[:3]
    )
    return EvidenceSelection(main, secondary, noise)


def _main_event_eligible(item: ExtractedEvidence) -> bool:
    classification = item.article.content_classification
    if classification is ContentClassification.REGULATORY_FILING:
        return _effective_importance(item) >= 15
    if classification in {
        ContentClassification.ANALYST_COMMENTARY,
        ContentClassification.OPINION,
    }:
        return item.depth_score >= 70 and _effective_importance(item) >= 20
    if (
        classification is ContentClassification.FACTUAL_FINANCIAL_REPORTING
        and item.depth_score <= EVIDENCE_DEPTH_SCORES[
            EvidenceDepth.PUBLIC_METADATA_DESCRIPTION
        ]
    ):
        return _effective_importance(item) >= 30
    return True


def _noise_candidate(item: ExtractedEvidence) -> bool:
    if item.article.content_classification in {
        ContentClassification.ANALYST_COMMENTARY,
        ContentClassification.OPINION,
    }:
        return True
    return (
        item.article.content_classification
        is ContentClassification.FACTUAL_FINANCIAL_REPORTING
        and item.depth_score <= EVIDENCE_DEPTH_SCORES[
            EvidenceDepth.PUBLIC_METADATA_DESCRIPTION
        ]
        and _effective_importance(item) < 30
    )


def _eligible(item: ExtractedEvidence) -> bool:
    article = item.article
    if (
        article.content_classification is ContentClassification.OPINION
        and item.evidence_depth is EvidenceDepth.HEADLINE_ONLY
    ):
        return False
    return article.relevance_score >= 35 and (
        article.importance_score >= 10
        or article.source_confidence_score >= 70
        or item.depth_score >= 70
    )


def _selection_score(item: ExtractedEvidence) -> float:
    article = item.article
    return round(
        article.relevance_score * 0.20
        + _effective_importance(item) * 0.25
        + article.source_confidence_score * 0.20
        + item.depth_score * 0.25
        + article.recency_score * 0.10,
        2,
    )


def _effective_importance(item: ExtractedEvidence) -> int:
    importance = item.article.importance_score
    if (item.article.filing_form or "").upper() not in {"4", "4/A"}:
        return importance
    for row in item.structured_ownership:
        if row.get("transaction_code") not in {"P", "S"}:
            continue
        try:
            shares = float(row.get("shares") or 0)
            price = float(row.get("price_per_share") or 0)
        except ValueError:
            continue
        if shares >= 10_000 or shares * price >= 100_000:
            return max(importance, 35)
    return importance


def _deduplicate(
    items: list[ExtractedEvidence],
) -> tuple[list[ExtractedEvidence], list[ExtractedEvidence]]:
    by_key: dict[str, ExtractedEvidence] = {}
    duplicates: list[ExtractedEvidence] = []
    for item in items:
        parts = urlsplit(item.original_url)
        url_key = f"{parts.netloc.lower()}{parts.path.rstrip('/').lower()}"
        headline_key = " ".join(sorted(_tokens(item)))
        key = url_key or headline_key
        current = by_key.get(key)
        if current is None:
            by_key[key] = item
        elif _selection_score(item) > _selection_score(current):
            duplicates.append(current)
            by_key[key] = item
        else:
            duplicates.append(item)
    return list(by_key.values()), duplicates


def _tokens(item: ExtractedEvidence) -> set[str]:
    text = f"{item.article.headline} {(item.material_event_summary or item.content or '')[:800]}"
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2 and token not in STOP_WORDS
    }


def _similar(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _same_event(item: ExtractedEvidence, existing: ExtractedEvidence, tokens: set[str]) -> bool:
    if (
        item.article.content_classification is ContentClassification.REGULATORY_FILING
        and existing.article.content_classification is ContentClassification.REGULATORY_FILING
        and item.article.filing_form != existing.article.filing_form
    ):
        return False
    return _similar(tokens, _tokens(existing)) >= 0.42
