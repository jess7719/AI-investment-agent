"""Market, fundamental, and valuation context with explicit data-quality labels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ComparisonReturn:
    symbol: str
    label: str
    thirty_day_change_percent: Decimal | None
    data_quality: str


@dataclass(frozen=True, slots=True)
class MarketContext:
    ticker: str
    as_of: date
    five_day_change_percent: Decimal | None
    thirty_day_change_percent: Decimal | None
    ninety_day_change_percent: Decimal | None
    distance_from_thirty_day_high_percent: Decimal | None
    distance_from_thirty_day_low_percent: Decimal | None
    average_twenty_day_volume: int | None
    latest_volume_vs_average_percent: Decimal | None
    recent_annualized_volatility_percent: Decimal | None
    comparisons: tuple[ComparisonReturn, ...]
    data_quality: str


@dataclass(frozen=True, slots=True)
class FundamentalMetric:
    name: str
    value: Decimal | None
    unit: str
    status: str
    as_of: datetime
    note: str
    source: str = ""
    reporting_period: str | None = None
    currency: str | None = None
    calculation_method: str | None = None
    decision_grade: bool = False
    official_source: bool = False
    price_date: date | None = None
    shares_outstanding_used: Decimal | None = None
    revenue_period_used: str | None = None
    market_capitalisation_used: Decimal | None = None
    resulting_ratio: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FundamentalContext:
    ticker: str
    metrics: tuple[FundamentalMetric, ...]
    peer_metrics: tuple[FundamentalMetric, ...]
    source: str
    overall_quality: str


@dataclass(frozen=True, slots=True)
class FuturesCurvePoint:
    contract: str
    maturity: str
    price: Decimal | None
    currency: str
    as_of: datetime
    source: str
    status: str


@dataclass(frozen=True, slots=True)
class CommodityProductContext:
    ticker: str
    product_name: str
    structure: str
    benchmark: str
    benchmark_code: str
    management_fee_percent: Decimal
    base_currency: str
    trading_currency: str
    currency_hedged: bool
    replication_method: str
    instruments: str
    roll_methodology: str
    issuer: str
    collateral_structure: str
    counterparty_note: str
    tracking_difference: Decimal | None
    source: str
    source_as_of: date


@dataclass(frozen=True, slots=True)
class CommodityETPContext:
    ticker: str
    underlying_name: str
    underlying_symbol: str
    as_of: date
    spot_price: Decimal | None
    front_month_price: Decimal | None
    five_day_change_percent: Decimal | None
    thirty_day_change_percent: Decimal | None
    ninety_day_change_percent: Decimal | None
    futures_curve: tuple[FuturesCurvePoint, ...]
    curve_structure: str
    roll_yield_direction: str
    curve_note: str
    product: CommodityProductContext
    liquidity_evidence: dict[str, object]
    factor_coverage: dict[str, object]
    data_quality: str
