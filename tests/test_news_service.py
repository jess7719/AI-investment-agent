from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_assistant.domain.news import (
    ContentClassification,
    InsiderTransaction,
    NewsArticle,
)
from investment_assistant.ports.news import (
    InvalidTickerError,
    NewsSourceError,
    NewsUnavailableError,
)
from investment_assistant.services.news_service import NewsService


class FakeSource:
    name = "Fake public source"
    enabled = True
    unavailable_reason = None

    def __init__(self, articles: list[NewsArticle] | None = None, fail: bool = False) -> None:
        self.articles = articles or []
        self.fail = fail
        self.requested_limit: int | None = None

    def fetch_recent(self, ticker: str, limit: int) -> list[NewsArticle]:
        self.requested_limit = limit
        if self.fail:
            raise NewsSourceError("network unavailable")
        return self.articles[:limit]


class DisabledSource:
    name = "Licensed database"
    enabled = False
    unavailable_reason = "disabled: credentials and licence permission required"

    def fetch_recent(self, ticker: str, limit: int) -> list[NewsArticle]:
        raise AssertionError("disabled adapter must not be called")


def article(
    number: int,
    headline: str,
    *,
    url: str | None = None,
    classification: ContentClassification = ContentClassification.FACTUAL_FINANCIAL_REPORTING,
) -> NewsArticle:
    return NewsArticle(
        ticker="NVDA",
        headline=headline,
        publisher="example.com",
        published_at=datetime.now(timezone.utc) - timedelta(hours=number),
        url=url or f"https://example.com/story-{number}",
        summary=None,
        content_classification=classification,
    )


def test_deduplicates_ranks_and_returns_top_ten() -> None:
    articles = [article(i, f"Nvidia routine update {i}") for i in range(12)]
    articles.extend(
        [
            article(20, "Nvidia earnings guidance rises", url="https://reuters.com/nvda"),
            article(
                21,
                "Nvidia earnings guidance rises",
                url="https://reuters.com/nvda?utm_source=duplicate",
            ),
        ]
    )
    source = FakeSource(articles)

    report = NewsService([source, DisabledSource()]).get_recent("nvda")

    assert len(report.articles) == 10
    assert report.articles[0].headline == "Nvidia earnings guidance rises"
    assert report.articles[0].importance_score >= 30
    assert report.articles[0].source_confidence_score == 80
    assert sum(item.headline == "Nvidia earnings guidance rises" for item in report.articles) == 1
    assert report.unavailable_sources == (
        "Licensed database - disabled: credentials and licence permission required",
    )
    assert source.requested_limit == 20


def test_returns_empty_report_when_no_news_is_found() -> None:
    report = NewsService([FakeSource()]).get_recent("NVDA")

    assert report.articles == ()
    assert report.warnings == ()


def test_rejects_invalid_ticker_without_calling_source() -> None:
    source = FakeSource()

    with pytest.raises(InvalidTickerError):
        NewsService([source]).get_recent("NVDA; DROP TABLE")

    assert source.requested_limit is None


def test_reports_total_network_failure() -> None:
    with pytest.raises(NewsUnavailableError, match="network unavailable"):
        NewsService([FakeSource(fail=True)]).get_recent("NVDA")


def test_official_first_separates_confidence_from_other_scores() -> None:
    regulatory = article(
        5,
        "SEC 8-K: Current report",
        url="https://sec.gov/filing",
        classification=ContentClassification.REGULATORY_FILING,
    )
    regulatory = replace(
        regulatory,
        filing_form="8-K",
        filing_description="Current report",
    )
    official = article(
        4,
        "SpaceX announces bond issuance",
        url="https://ir.spacex.com/release",
        classification=ContentClassification.OFFICIAL_COMPANY_RELEASE,
    )
    factual = article(
        1,
        "SPCX shares move after launch",
        classification=ContentClassification.FACTUAL_FINANCIAL_REPORTING,
    )
    opinion = article(
        0,
        "Prediction: SPCX will double",
        classification=ContentClassification.OPINION,
    )

    report = NewsService(
        [
            FakeSource([regulatory]),
            FakeSource([official]),
            FakeSource([factual]),
            FakeSource([opinion]),
        ]
    ).get_recent("SPCX", official_first=True)

    assert [item.source_confidence_score for item in report.articles] == [100, 95, 80, 35]
    assert report.articles[0].content_classification is ContentClassification.REGULATORY_FILING
    assert report.official_first is True


def test_routine_sec_filing_does_not_automatically_beat_material_reporting() -> None:
    form3 = replace(
        article(
            0,
            "SEC 3: Initial ownership disclosure",
            classification=ContentClassification.REGULATORY_FILING,
        ),
        filing_form="3",
    )
    reporting = article(2, "Nvidia earnings results and higher guidance")

    report = NewsService([FakeSource([form3]), FakeSource([reporting])]).get_recent(
        "NVDA", official_first=True
    )

    assert report.articles[0].headline == reporting.headline
    assert report.articles[0].source_confidence_score == 80
    assert report.articles[1].source_confidence_score == 100
    assert report.articles[1].importance_score == 5


@pytest.mark.parametrize(
    ("form", "description", "items", "expected"),
    [
        ("10-K", "Annual report", (), 65),
        ("10-Q", "Quarterly report", (), 65),
        ("S-1", "Registration statement", (), 60),
        ("424B4", "Final prospectus", (), 60),
        ("8-K", "Results of operations", ("2.02",), 65),
        ("8-K", "Completion of acquisition", ("2.01",), 60),
        ("8-K", "Routine current report", ("8.01",), 20),
        ("3", "Initial ownership disclosure", (), 5),
    ],
)
def test_filing_type_importance_rules(
    form: str, description: str, items: tuple[str, ...], expected: int
) -> None:
    filing = replace(
        article(
            10,
            f"SEC {form}: {description}",
            classification=ContentClassification.REGULATORY_FILING,
        ),
        filing_form=form,
        filing_description=description,
        filing_items=items,
    )

    scored = NewsService([FakeSource([filing])]).get_recent("NVDA").articles[0]

    assert scored.importance_score == expected


def test_meaningful_open_market_form4_is_medium_importance() -> None:
    filing = replace(
        article(
            10,
            "SEC 4: Insider transaction",
            classification=ContentClassification.REGULATORY_FILING,
        ),
        filing_form="4",
        insider_transactions=(
            InsiderTransaction(
                reporting_person="Example Insider",
                transaction_date=None,
                transaction_code="S",
                transaction_type="open-market sale",
                shares=Decimal("12500"),
                price_per_share=Decimal("42.50"),
            ),
        ),
    )

    scored = NewsService([FakeSource([filing])]).get_recent("NVDA").articles[0]

    assert scored.importance_score == 35
    assert scored.importance_score < 60


def test_form4_without_transaction_details_stays_low_importance() -> None:
    filing = replace(
        article(
            10,
            "SEC 4: Insider transaction",
            classification=ContentClassification.REGULATORY_FILING,
        ),
        filing_form="4",
    )

    scored = NewsService([FakeSource([filing])]).get_recent("NVDA").articles[0]

    assert scored.importance_score == 8
