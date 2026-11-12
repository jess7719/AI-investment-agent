from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pandas as pd
import pytest

from investment_assistant.adapters.yfinance_market_data import (
    YFinanceMarketDataAdapter,
    comparison_symbols,
)
from investment_assistant.domain.context import CommodityETPContext, CommodityProductContext
from investment_assistant.domain.deep_report import DeepResearchReport
from investment_assistant.domain.evidence import EvidenceDepth, ExtractedEvidence
from investment_assistant.domain.models import StockMarketSnapshot
from investment_assistant.domain.news import ContentClassification, NewsArticle
from investment_assistant.services.deep_report_quality import validate_deep_report_quality
from investment_assistant.services.deep_report_service import DeepReportService
from investment_assistant.services.research_service import PreparedResearch, build_evidence_payload
from tests.test_deep_report import deep_report_data


def _snapshot() -> StockMarketSnapshot:
    return StockMarketSnapshot(
        ticker="BRNT.L",
        as_of=date(2026, 10, 16),
        latest_close=Decimal("42.50"),
        previous_close=Decimal("42.00"),
        daily_change_percent=Decimal("1.19"),
        five_day_change_percent=Decimal("-2.50"),
        volume=1_500,
    )


def _commodity_context() -> CommodityETPContext:
    return CommodityETPContext(
        ticker="BRNT.L",
        underlying_name="Brent crude oil futures",
        underlying_symbol="BZ=F",
        as_of=date(2026, 10, 16),
        spot_price=None,
        front_month_price=Decimal("91.25"),
        five_day_change_percent=Decimal("-1.2"),
        thirty_day_change_percent=Decimal("5.4"),
        ninety_day_change_percent=Decimal("8.1"),
        futures_curve=(),
        curve_structure="contango",
        roll_yield_direction="likely negative",
        curve_note="Second month is above front month.",
        product=CommodityProductContext(
            ticker="BRNT.L",
            product_name="WisdomTree Brent Crude Oil",
            structure="ETC debt security",
            benchmark="Bloomberg Commodity Brent Crude Subindex 4W Total Return",
            benchmark_code="BCOMCO4T",
            management_fee_percent=Decimal("0.49"),
            base_currency="USD",
            trading_currency="USD",
            currency_hedged=False,
            replication_method="fully funded collateralised swap",
            instruments="swaps referencing Brent futures",
            roll_methodology="pre-determined index roll",
            issuer="WisdomTree Commodity Securities Limited",
            collateral_structure="collateral marked daily",
            counterparty_note="swap counterparty exposure",
            tracking_difference=None,
            source="WisdomTree",
            source_as_of=date(2026, 6, 5),
        ),
        liquidity_evidence={
            "exchange_trading_volume": "supplied",
            "bid_ask_spread": "unavailable",
            "underlying_market_liquidity": "not measured",
            "market_maker_liquidity": "unavailable",
        },
        factor_coverage={},
        data_quality="mixed issuer and third-party evidence",
    )


def _commodity_report() -> DeepResearchReport:
    data = deep_report_data()
    data["long_term_thesis"] = {
        "status": "unchanged",
        "core_thesis": (
            "Oil supply and demand drive Brent, while contango can create negative roll yield "
            "and backwardation can create positive roll yield for this ETC."
        ),
        "supporting_factors": ["A tighter oil market could support Brent prices."],
        "threats": ["Contango and product costs can reduce long-term returns."],
        "thesis_invalidation_conditions": ["Persistent oversupply weakens Brent prices."],
    }
    data["limitations"] = ["The full dated futures curve is unavailable."]
    data["confidence"]["fundamentals"] = {
        "level": "medium",
        "reason": "Issuer product details are available but some curve data are unavailable.",
    }
    return DeepResearchReport.model_validate(data)


def _evidence() -> dict[str, object]:
    payload, _, _, _ = build_evidence_payload(
        _snapshot(),
        [],
        date(2026, 10, 18),
        commodity_context=_commodity_context(),
    )
    return payload


def test_commodity_etp_omits_equity_fundamentals_without_calling_them_gaps() -> None:
    payload = _evidence()

    assert payload["scope"]["asset_type"] == "commodity_etp"
    fundamentals = payload["fundamental_and_valuation_context"]
    assert fundamentals["applicability"] == "not_applicable"
    assert "P/E" not in payload["scope"]["limitations"]
    assert "revenue" not in payload["scope"]["limitations"]
    assert payload["commodity_etp_context"]["curve_structure"] == "contango"
    assert payload["commodity_etp_context"]["front_month_price"] == "91.25"


def test_brnt_uses_oil_benchmarks_instead_of_spy() -> None:
    comparisons = comparison_symbols("BRNT.L")

    assert comparisons[0] == ("BZ=F", "primary Brent futures benchmark")
    assert {symbol for symbol, _ in comparisons} == {"BZ=F", "CL=F"}
    assert "SPY" not in {symbol for symbol, _ in comparisons}


def test_metadata_only_factor_is_a_lead_not_verified_evidence() -> None:
    article = NewsArticle(
        ticker="BRNT.L",
        headline="Shipping disruption raises oil supply concerns",
        publisher="Reuters",
        published_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
        url="https://example.com/oil",
        summary="Metadata description about a shipping disruption.",
        content_classification=ContentClassification.FACTUAL_FINANCIAL_REPORTING,
    )
    item = ExtractedEvidence(
        article=article,
        evidence_depth=EvidenceDepth.PUBLIC_METADATA_DESCRIPTION,
        content=article.summary,
        original_url=article.url,
    )

    payload, _, _, _ = build_evidence_payload(
        _snapshot(), [item], commodity_context=_commodity_context()
    )
    factor = payload["commodity_etp_context"]["factor_coverage"]["geopolitical"]

    assert factor["references"] == []
    assert factor["metadata_leads_excluded_from_verified_factors"] == ["[News 1]"]


def test_deep_request_uses_commodity_framework_and_roll_yield() -> None:
    research = PreparedResearch(
        ticker="BRNT.L",
        report_date=date(2026, 10, 18),
        evidence=_evidence(),
        evidence_json="{}",
        allowed_references=frozenset({"[Market Data]"}),
        news_count=0,
        filing_count=0,
    )

    prepared = DeepReportService(reports_root=Path("reports")).prepare(research)
    request = json.loads(prepared.request_json)

    framework = request["report_request"]["analysis_framework"]
    assert request["report_request"]["asset_type"] == "commodity_etp"
    assert "futures curve and roll yield" in framework["valuation_replacement"]
    assert "revenue" in framework["equity_metrics_not_applicable"]


def test_contango_and_backwardation_are_required_in_long_term_thesis() -> None:
    report = _commodity_report()
    validate_deep_report_quality(report, evidence=_evidence(), configured_profile_fields=())

    data = report.model_dump(mode="json")
    data["long_term_thesis"]["core_thesis"] = "Oil prices may move as conditions change."
    data["long_term_thesis"]["threats"] = ["Oil prices can fall."]
    with pytest.raises(ValueError, match="futures curve"):
        validate_deep_report_quality(
            DeepResearchReport.model_validate(data),
            evidence=_evidence(),
            configured_profile_fields=(),
        )


def test_low_exchange_volume_alone_does_not_imply_poor_liquidity() -> None:
    data = _commodity_report().model_dump(mode="json")
    data["limitations"] = ["Low exchange trading volume means poor liquidity."]
    with pytest.raises(ValueError, match="volume alone"):
        validate_deep_report_quality(
            DeepResearchReport.model_validate(data),
            evidence=_evidence(),
            configured_profile_fields=(),
        )

    data["limitations"] = [
        "Low exchange volume alone does not prove poor liquidity; spread and market-maker "
        "evidence are unavailable."
    ]
    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=_evidence(),
        configured_profile_fields=(),
    )


def test_adapter_collects_front_month_performance_and_product_structure() -> None:
    dates = pd.date_range("2026-05-01", periods=100, freq="B")
    history = pd.DataFrame(
        {"Close": [70 + index / 10 for index in range(100)], "Volume": [1000] * 100},
        index=dates,
    )
    adapter = YFinanceMarketDataAdapter(
        ticker_factory=lambda ticker: type(
            "FakeTicker",
            (),
            {"history": lambda self, **kwargs: history},
        )()
    )

    context = adapter.get_commodity_etp_context("BRNT.L")

    assert context.front_month_price == Decimal("79.9")
    assert context.thirty_day_change_percent is not None
    assert context.ninety_day_change_percent is not None
    assert context.product.management_fee_percent == Decimal("0.49")
    assert context.product.replication_method == "synthetic - fully funded collateralised swap"
    assert context.curve_structure == "unavailable"
