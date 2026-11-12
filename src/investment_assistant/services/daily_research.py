"""The future daily workflow, deliberately kept independent of vendors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from investment_assistant.ports.providers import (
    MarketDataProvider,
    NewsProvider,
    PortfolioRepository,
    ReasoningEngine,
)


@dataclass(slots=True)
class DailyResearchService:
    portfolio: PortfolioRepository
    market_data: MarketDataProvider
    news: NewsProvider
    reasoner: ReasoningEngine

    def collect_evidence(self, report_date: date) -> dict[str, object]:
        """Collect traceable facts without asking the language model for facts."""

        holdings = self.portfolio.list_holdings()
        return {
            "holdings": holdings,
            "prices": self.market_data.get_prices(holdings),
            "fundamentals": self.market_data.get_fundamentals(holdings),
            "news": self.news.get_company_news(
                holdings,
                start_date=report_date - timedelta(days=1),
                end_date=report_date,
            ),
        }
