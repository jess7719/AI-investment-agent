from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from investment_assistant.adapters.yfinance_market_data import (
    MarketDataError,
    YFinanceMarketDataAdapter,
)


class FakeTicker:
    def __init__(self, history: pd.DataFrame) -> None:
        self._history = history
        self.history_arguments: dict[str, object] | None = None

    def history(self, **kwargs: object) -> pd.DataFrame:
        self.history_arguments = kwargs
        return self._history


def make_history(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Close": closes,
            "Volume": [1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_400_000, 1_500_000][
                : len(closes)
            ],
        },
        index=pd.date_range("2026-07-06", periods=len(closes), freq="B"),
    )


def test_calculates_snapshot_from_six_trading_days() -> None:
    fake_ticker = FakeTicker(make_history([100, 102, 104, 103, 105, 110]))
    adapter = YFinanceMarketDataAdapter(ticker_factory=lambda ticker: fake_ticker)

    snapshot = adapter.get_stock_snapshot(" nvda ")

    assert snapshot.ticker == "NVDA"
    assert snapshot.as_of == date(2026, 7, 13)
    assert snapshot.latest_close == Decimal("110.0")
    assert snapshot.previous_close == Decimal("105.0")
    assert snapshot.daily_change_percent == pytest.approx(Decimal("4.7619047619"))
    assert snapshot.five_day_change_percent == Decimal("10.0")
    assert snapshot.volume == 1_500_000
    assert fake_ticker.history_arguments == {
        "period": "1mo",
        "interval": "1d",
        "auto_adjust": False,
        "actions": False,
        "timeout": 10,
        "raise_errors": True,
    }


def test_rejects_too_little_history() -> None:
    fake_ticker = FakeTicker(make_history([100, 101]))
    adapter = YFinanceMarketDataAdapter(ticker_factory=lambda ticker: fake_ticker)

    with pytest.raises(MarketDataError, match="At least 6 trading days"):
        adapter.get_stock_snapshot("NEW")


def test_rejects_empty_ticker() -> None:
    adapter = YFinanceMarketDataAdapter()

    with pytest.raises(MarketDataError, match="cannot be empty"):
        adapter.get_stock_snapshot("  ")


def test_default_adapter_configures_writable_project_cache(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured: list[str] = []
    monkeypatch.setattr("investment_assistant.adapters.yfinance_market_data.yf.Ticker", yf_ticker)
    monkeypatch.setattr(
        "investment_assistant.adapters.yfinance_market_data.yf.set_tz_cache_location",
        configured.append,
    )

    YFinanceMarketDataAdapter(ticker_factory=yf_ticker, cache_dir=tmp_path)

    assert configured == [str(tmp_path)]


def yf_ticker(ticker: str) -> object:
    return ticker
