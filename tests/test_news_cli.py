from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from investment_assistant.cli import print_news_report
from investment_assistant.domain.news import (
    ContentClassification,
    InsiderTransaction,
    NewsArticle,
)
from investment_assistant.services.news_service import NewsReport


def test_prints_missing_summary_and_disabled_sources(capsys: Any) -> None:
    report = NewsReport(
        ticker="NVDA",
        articles=(
            NewsArticle(
                ticker="NVDA",
                headline="Nvidia test headline",
                publisher="reuters.com",
                published_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
                url="https://reuters.com/example",
                summary=None,
                content_classification=ContentClassification.FACTUAL_FINANCIAL_REPORTING,
                relevance_score=90,
                importance_score=55,
                source_confidence_score=80,
            ),
        ),
        unavailable_sources=("FT API — disabled",),
        warnings=(),
    )

    print_news_report(report)
    output = capsys.readouterr().out

    assert "Nvidia test headline" in output
    assert "factual financial reporting" in output
    assert "relevance 90/100, importance 55/100, source confidence 80/100" in output
    assert "Not provided by source" in output
    assert "FT API — disabled" in output


def test_prints_no_news_message(capsys: Any) -> None:
    print_news_report(NewsReport("NVDA", (), (), ()))

    assert "No recent news found for NVDA." in capsys.readouterr().out


def test_prints_structured_form4_details_without_sentiment(capsys: Any) -> None:
    article = NewsArticle(
        ticker="SPCX",
        headline="SEC 4: FORM 4",
        publisher="SEC EDGAR",
        published_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        url="https://www.sec.gov/example.xml",
        summary=None,
        content_classification=ContentClassification.REGULATORY_FILING,
        filing_form="4",
        insider_transactions=(
            InsiderTransaction(
                reporting_person="Example Insider",
                transaction_date=date(2026, 6, 16),
                transaction_code="P",
                transaction_type="open-market purchase",
                shares=Decimal("12500"),
                price_per_share=Decimal("42.50"),
            ),
        ),
    )

    print_news_report(NewsReport("SPCX", (article,), (), (), True))
    output = capsys.readouterr().out

    assert "person=Example Insider" in output
    assert "date=2026-06-16" in output
    assert "type=open-market purchase" in output
    assert "shares=12,500" in output
    assert "price=$42.5" in output
    assert "bullish" not in output.lower()
    assert "bearish" not in output.lower()
