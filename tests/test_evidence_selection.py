from dataclasses import replace
from datetime import datetime, timezone

from investment_assistant.domain.evidence import EvidenceDepth, ExtractedEvidence
from investment_assistant.domain.news import ContentClassification, NewsArticle
from investment_assistant.services.evidence_selection import select_evidence


def item(
    headline: str,
    depth: EvidenceDepth,
    classification: ContentClassification,
    url: str,
) -> ExtractedEvidence:
    article = NewsArticle(
        ticker="SPCX",
        headline=headline,
        publisher="Example",
        published_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        url=url,
        summary=None,
        content_classification=classification,
        relevance_score=80,
        importance_score=40,
        source_confidence_score=80,
        recency_score=90,
    )
    return ExtractedEvidence(article, depth, "Detailed event evidence", url)


def test_groups_related_events_prefers_depth_and_excludes_weak_opinion() -> None:
    first = item(
        "SpaceX completes major bond financing",
        EvidenceDepth.HEADLINE_ONLY,
        ContentClassification.FACTUAL_FINANCIAL_REPORTING,
        "https://a/1",
    )
    richer = replace(
        item(
            "SpaceX completes major bond financing transaction",
            EvidenceDepth.OFFICIAL_FILING_EXTRACT,
            ContentClassification.REGULATORY_FILING,
            "https://sec/2",
        ),
        content="Major bond financing transaction details",
    )
    opinion = item(
        "Prediction: SpaceX will double",
        EvidenceDepth.HEADLINE_ONLY,
        ContentClassification.OPINION,
        "https://opinion/3",
    )

    selection = select_evidence([first, richer, opinion])

    assert len(selection.main_clusters) == 1
    assert selection.main_clusters[0].selected is richer
    assert opinion not in selection.secondary_items
    assert opinion in selection.noise_items


def test_distinct_sec_forms_are_not_grouped_as_one_event() -> None:
    form8k = item(
        "SEC 8-K current report",
        EvidenceDepth.OFFICIAL_FILING_EXTRACT,
        ContentClassification.REGULATORY_FILING,
        "https://sec/8k",
    )
    form3 = item(
        "SEC Form 3 ownership report",
        EvidenceDepth.OFFICIAL_FILING_EXTRACT,
        ContentClassification.REGULATORY_FILING,
        "https://sec/3",
    )
    form8k = replace(form8k, article=replace(form8k.article, filing_form="8-K"))
    form3 = replace(form3, article=replace(form3.article, filing_form="3", importance_score=5))

    selection = select_evidence([form8k, form3])

    assert all(len(cluster.items) == 1 for cluster in selection.main_clusters)
    assert len(selection.main_clusters) == 1
    assert form3 in selection.secondary_items


def test_meaningful_open_market_form4_can_be_a_main_development() -> None:
    form4 = item(
        "SEC Form 4 ownership transaction",
        EvidenceDepth.OFFICIAL_FILING_EXTRACT,
        ContentClassification.REGULATORY_FILING,
        "https://sec/4",
    )
    form4 = replace(
        form4,
        article=replace(form4.article, filing_form="4", importance_score=8),
        structured_ownership=(
            {
                "transaction_code": "S",
                "shares": "12000",
                "price_per_share": "105.25",
            },
        ),
    )

    selection = select_evidence([form4])

    assert selection.main_clusters[0].selected is form4


def test_metadata_only_analyst_view_is_not_promoted_to_main_event() -> None:
    analyst_view = item(
        "Analyst says SpaceX will not be profitable until 2027",
        EvidenceDepth.PUBLIC_METADATA_DESCRIPTION,
        ContentClassification.ANALYST_COMMENTARY,
        "https://example/analyst",
    )

    selection = select_evidence([analyst_view])

    assert selection.main_clusters == ()
    assert analyst_view in selection.secondary_items
    assert analyst_view in selection.noise_items


def test_low_importance_metadata_reporting_is_not_promoted_to_main_event() -> None:
    tangential = item(
        "Broker eyes more technology IPOs after SpaceX offering",
        EvidenceDepth.PUBLIC_METADATA_DESCRIPTION,
        ContentClassification.FACTUAL_FINANCIAL_REPORTING,
        "https://example/tangential",
    )
    tangential = replace(
        tangential,
        article=replace(tangential.article, importance_score=20),
    )

    selection = select_evidence([tangential])

    assert selection.main_clusters == ()
    assert tangential in selection.noise_items
