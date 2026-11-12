"""Provider contracts used by the research workflow."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from investment_assistant.domain.models import (
    FundamentalSnapshot,
    Holding,
    NewsItem,
    PriceSnapshot,
    StockMarketSnapshot,
)


class PortfolioRepository(Protocol):
    def list_holdings(self) -> list[Holding]: ...


class MarketDataProvider(Protocol):
    def get_prices(self, holdings: Sequence[Holding]) -> list[PriceSnapshot]: ...

    def get_fundamentals(self, holdings: Sequence[Holding]) -> list[FundamentalSnapshot]: ...


class SingleStockMarketDataProvider(Protocol):
    def get_stock_snapshot(self, ticker: str) -> StockMarketSnapshot: ...


class NewsProvider(Protocol):
    def get_company_news(
        self, holdings: Sequence[Holding], start_date: date, end_date: date
    ) -> list[NewsItem]: ...


class ReasoningEngine(Protocol):
    def analyse(self, instructions: str, evidence: str) -> str: ...
