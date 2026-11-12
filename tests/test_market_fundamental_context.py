from datetime import datetime

import pandas as pd

from investment_assistant.adapters.yfinance_market_data import YFinanceMarketDataAdapter


def history(multiplier: float = 1.0) -> pd.DataFrame:
    index = pd.date_range(end=datetime(2026, 7, 14), periods=120, freq="B")
    return pd.DataFrame(
        {
            "Close": [(100 + number) * multiplier for number in range(120)],
            "Volume": [1_000_000 + number * 1000 for number in range(120)],
        },
        index=index,
    )


class FakeTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, **kwargs: object) -> pd.DataFrame:
        return history({"SPY": 2.0, "RKLB": 0.5}.get(self.symbol, 1.0))

    @property
    def info(self) -> dict[str, object]:
        return {
            "totalRevenue": 10_000_000,
            "revenueGrowth": 0.25,
            "netIncomeToCommon": -500_000,
            "operatingMargins": -0.05,
            "freeCashflow": 1_000_000,
            "totalCash": 5_000_000,
            "totalDebt": 2_000_000,
            "sharesOutstanding": 1_000_000,
            "marketCap": 100_000_000,
            "trailingPE": -20,
            "priceToSalesTrailing12Months": 10,
        }


def test_calculates_extended_market_context_and_comparisons() -> None:
    context = YFinanceMarketDataAdapter(FakeTicker).get_market_context("SPCX")

    assert context.five_day_change_percent is not None
    assert context.thirty_day_change_percent is not None
    assert context.ninety_day_change_percent is not None
    assert context.average_twenty_day_volume is not None
    assert context.latest_volume_vs_average_percent is not None
    assert context.recent_annualized_volatility_percent is not None
    assert [comparison.symbol for comparison in context.comparisons] == ["SPY", "RKLB"]


def test_marks_unavailable_and_not_meaningful_fundamental_values() -> None:
    context = YFinanceMarketDataAdapter(FakeTicker).get_fundamental_context("SPCX")
    metrics = {metric.name: metric for metric in context.metrics}

    assert metrics["revenue growth"].status == "third-party reported or estimated"
    assert metrics["P/E"].status == "not meaningful"
    assert context.peer_metrics
    assert "verify" in metrics["cash"].note
    assert metrics["cash"].source == "Yahoo Finance/yfinance third-party snapshot"
    assert metrics["cash"].currency is None
    assert metrics["cash"].reporting_period is None
    assert metrics["cash"].decision_grade is False
    assert "not independently reproduced" in metrics["market capitalisation"].calculation_method
