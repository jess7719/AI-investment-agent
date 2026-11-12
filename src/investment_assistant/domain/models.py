"""Small, explicit data structures shared across the application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    exchange: str
    company: str
    quantity: Decimal
    average_cost: Decimal
    currency: str
    thesis: str


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    symbol: str
    exchange: str
    price: Decimal
    currency: str
    change_percent: Decimal
    observed_at: datetime
    source_url: str


@dataclass(frozen=True, slots=True)
class NewsItem:
    headline: str
    summary: str
    published_at: datetime
    source_name: str
    source_url: str
    related_symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FundamentalSnapshot:
    symbol: str
    exchange: str
    as_of: datetime
    metrics: dict[str, Decimal | str | None]
    source_url: str


@dataclass(frozen=True, slots=True)
class StockMarketSnapshot:
    """Latest daily values calculated from a stock's price history."""

    ticker: str
    as_of: date
    latest_close: Decimal
    previous_close: Decimal
    daily_change_percent: Decimal
    five_day_change_percent: Decimal
    volume: int
