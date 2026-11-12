from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.adapters.yfinance_market_data import MarketDataError
from investment_assistant.domain.models import StockMarketSnapshot
from investment_assistant.domain.context import FundamentalContext, FundamentalMetric
from investment_assistant.domain.news import ContentClassification, NewsArticle
from investment_assistant.domain.research import (
    GeneratedResearchReport,
    TokenUsage,
)
from investment_assistant.ports.research import (
    MalformedResearchResponseError,
    ResearchDataUnavailableError,
)
from investment_assistant.services.news_service import NewsReport
from investment_assistant.services.research_service import (
    ResearchService,
    _fundamental_evidence_context,
)
from test_openai_reasoner import valid_report


class FakeMarketData:
    def get_stock_snapshot(self, ticker: str) -> StockMarketSnapshot:
        return StockMarketSnapshot(
            ticker=ticker,
            as_of=date(2026, 7, 13),
            latest_close=Decimal("42.50"),
            previous_close=Decimal("41.00"),
            daily_change_percent=Decimal("3.6585"),
            five_day_change_percent=Decimal("7.25"),
            volume=1_234_567,
        )


class FailingMarketData:
    def get_stock_snapshot(self, ticker: str) -> StockMarketSnapshot:
        raise MarketDataError("network unavailable")


class FakeNews:
    def __init__(self, articles: tuple[NewsArticle, ...]) -> None:
        self.articles = articles
        self.official_first: bool | None = None

    def get_recent(self, ticker: str, **kwargs: object) -> NewsReport:
        self.official_first = bool(kwargs.get("official_first"))
        return NewsReport(ticker, self.articles, (), (), True)


def sample_article() -> NewsArticle:
    return NewsArticle(
        ticker="SPCX",
        headline="SPCX reports a company update",
        publisher="Example News",
        published_at=datetime(2026, 7, 13, 12, tzinfo=timezone.utc),
        url="https://example.com/story",
        summary=None,
        content_classification=ContentClassification.FACTUAL_FINANCIAL_REPORTING,
        relevance_score=90,
        importance_score=30,
        source_confidence_score=80,
        recency_score=85,
        ranking_score=70,
    )


class FakeReasoner:
    def __init__(self, reference: str = "[News 1]") -> None:
        self.reference = reference
        self.received = ""

    def generate(self, evidence_json: str) -> GeneratedResearchReport:
        self.received = evidence_json
        return GeneratedResearchReport(
            valid_report(self.reference),
            "gpt-test",
            TokenUsage(1200, 700, 1900),
        )


def test_prepares_bounded_evidence_with_numbered_references(tmp_path: Path) -> None:
    articles = tuple(
        replace(sample_article(), headline=("X" * 500) + str(index), url=f"https://x/{index}")
        for index in range(12)
    )
    news = FakeNews(articles)
    service = ResearchService(FakeMarketData(), news, tmp_path, date(2026, 7, 14))

    prepared = service.prepare("spcx")

    assert news.official_first is True
    assert prepared.news_count == 8
    assert prepared.filing_count == 0
    assert "[Market Data]" in prepared.allowed_references
    assert "[News 8]" in prepared.allowed_references
    assert "[News 9]" not in prepared.allowed_references
    items = prepared.evidence["ranked_information_items"]
    assert isinstance(items, list)
    assert len(items[0]["headline"]) == 240
    assert "article body" in prepared.evidence_json
    assert prepared.evidence["market_data"]["latest_close"] == "42.50"
    assert prepared.evidence["market_data"]["daily_percentage_change"] == "3.66"
    assert (
        prepared.evidence["raw_numeric_evidence"]["market_data"]["daily_percentage_change"]
        == "3.6585"
    )
    assert "raw_numeric_evidence" not in prepared.evidence_json


def test_saves_validated_markdown_and_token_usage(tmp_path: Path) -> None:
    service = ResearchService(
        FakeMarketData(), FakeNews((sample_article(),)), tmp_path, date(2026, 7, 14)
    )
    prepared = service.prepare("SPCX")

    completed = service.generate_and_save(prepared, FakeReasoner())

    assert completed.path == tmp_path / "2026-07-14" / "SPCX.md"
    assert completed.path.exists()
    assert "# Evidence-grounded research report: SPCX" in completed.markdown
    assert "input=1200, output=700, total=1900" in completed.markdown
    assert "[News 1]" in completed.markdown


def test_rejects_unknown_model_reference_before_saving(tmp_path: Path) -> None:
    service = ResearchService(
        FakeMarketData(), FakeNews((sample_article(),)), tmp_path, date(2026, 7, 14)
    )
    prepared = service.prepare("SPCX")

    with pytest.raises(MalformedResearchResponseError, match="unsupported evidence reference"):
        service.generate_and_save(prepared, FakeReasoner("[News 99]"))

    assert not service.report_path("SPCX").exists()


def test_market_data_failure_is_reported_without_calling_ai(tmp_path: Path) -> None:
    service = ResearchService(
        FailingMarketData(), FakeNews((sample_article(),)), tmp_path, date(2026, 7, 14)
    )

    with pytest.raises(ResearchDataUnavailableError, match="Market data unavailable"):
        service.prepare("SPCX")


def test_empty_news_is_reported_without_calling_ai(tmp_path: Path) -> None:
    service = ResearchService(FakeMarketData(), FakeNews(()), tmp_path, date(2026, 7, 14))

    with pytest.raises(ResearchDataUnavailableError, match="no ranked items"):
        service.prepare("SPCX")


def metric(
    name: str,
    value: str,
    *,
    source: str,
    official: bool,
    decision_grade: bool,
) -> FundamentalMetric:
    return FundamentalMetric(
        name=name,
        value=Decimal(value),
        unit="currency",
        status="verified" if official else "third-party reported",
        as_of=datetime(2026, 6, 30, tzinfo=timezone.utc),
        note="",
        source=source,
        reporting_period="six months ended 2026-06-30",
        currency="USD",
        calculation_method="reported value from the cited document",
        decision_grade=decision_grade,
        official_source=official,
    )


def test_excludes_incomplete_third_party_fundamentals_from_decision_evidence() -> None:
    yahoo = FundamentalMetric(
        name="revenue",
        value=Decimal("14000000000"),
        unit="currency",
        status="third-party reported",
        as_of=datetime(2026, 7, 17, tzinfo=timezone.utc),
        note="verify",
        source="Yahoo Finance",
        currency="USD",
        calculation_method="provider-supplied",
    )
    context = FundamentalContext("SPCX", (yahoo,), (), "Yahoo Finance", "unverified")

    payload = _fundamental_evidence_context(context)

    assert payload is not None
    assert payload["decision_grade_metrics"] == []
    assert payload["excluded_non_decision_grade_metrics"][0]["name"] == "revenue"
    assert "reporting period" in payload["excluded_non_decision_grade_metrics"][0]["reason"]


def test_official_value_wins_and_third_party_discrepancy_is_flagged() -> None:
    third_party = metric(
        "revenue",
        "14000000000",
        source="Yahoo Finance",
        official=False,
        decision_grade=False,
    )
    official = metric(
        "revenue",
        "15000000000",
        source="SEC filing 10-Q",
        official=True,
        decision_grade=True,
    )
    context = FundamentalContext(
        "SPCX", (third_party, official), (), "multiple sources", "reconciled"
    )

    payload = _fundamental_evidence_context(context)

    assert payload is not None
    assert payload["decision_grade_metrics"][0]["value"] == "15.00 billion"
    assert payload["decision_grade_metrics"][0]["source"] == "SEC filing 10-Q"
    assert payload["discrepancies"][0]["resolution"] == (
        "official value used; discrepancy flagged"
    )


def test_valuation_metric_includes_reproducible_calculation_inputs() -> None:
    valuation = FundamentalMetric(
        name="price-to-sales",
        value=Decimal("10"),
        unit="multiple",
        status="calculated from official inputs",
        as_of=datetime(2026, 6, 30, tzinfo=timezone.utc),
        note="",
        source="SEC filing and official market record",
        reporting_period="year ended 2026-06-30",
        currency="USD",
        calculation_method="market capitalisation divided by annual revenue",
        decision_grade=True,
        official_source=True,
        price_date=date(2026, 6, 30),
        shares_outstanding_used=Decimal("1000000"),
        revenue_period_used="year ended 2026-06-30",
        market_capitalisation_used=Decimal("100000000"),
        resulting_ratio=Decimal("10"),
    )
    context = FundamentalContext("SPCX", (valuation,), (), "official", "verified")

    payload = _fundamental_evidence_context(context)

    assert payload is not None
    calculation = payload["decision_grade_metrics"][0]["valuation_calculation"]
    assert calculation == {
        "market_capitalisation": "100.00 million",
        "revenue_period_used": "year ended 2026-06-30",
        "shares_outstanding_used": "1.00 million",
        "price_date": "2026-06-30",
        "calculation_method": "market capitalisation divided by annual revenue",
        "resulting_ratio": "10.00",
    }
