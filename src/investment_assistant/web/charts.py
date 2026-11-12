"""Date-aligned price and volume series with explicit quote units and adjustments."""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from threading import Lock
from dataclasses import asdict

import yfinance as yf

from investment_assistant.domain.assets import AssetType, asset_profile_for
from investment_assistant.adapters.yfinance_market_data import (
    YFinanceMarketDataAdapter, peer_symbol,
)


def aligned_rows(prices: list[dict], benchmark: list[dict]) -> list[dict]:
    other = {row["date"]: row for row in benchmark if _positive(row.get("close"))}
    common = [row for row in prices if row["date"] in other and _positive(row.get("close"))]
    common.sort(key=lambda row: row["date"])
    if not common:
        return []
    base = common[0]["close"]
    comparison_base = other[common[0]["date"]]["close"]
    return [{**row, "return": round((row["close"] / base - 1) * 100, 4),
             "benchmark_return": round((other[row["date"]]["close"] / comparison_base - 1) * 100, 4)}
            for row in common]


def _positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


class ChartService:
    def __init__(self, factory=yf.Ticker):
        self.factory = factory
        self.cache = {}
        self.lock = Lock()

    def get(self, ticker: str, window: str):
        key = (ticker, window)
        with self.lock:
            cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 900:
            return cached[1]
        asset = asset_profile_for(ticker)
        item = self.factory(ticker)
        # auto_adjust=True adjusts splits and dividends consistently for both series.
        history = item.history(period="1y", interval="1d", auto_adjust=True, actions=False,
                               timeout=10, raise_errors=True)
        if history is None or history.empty:
            raise ValueError("Daily price history is unavailable. Try another investment.")
        metadata = getattr(item, "history_metadata", {}) or {}
        quote_type = str(metadata.get("instrumentType", ""))
        if asset.asset_type == AssetType.COMMODITY_ETP:
            symbol, label = asset.underlying_symbol, asset.underlying_name
        elif ticker.endswith(".L"):
            symbol, label = "^FTSE", "FTSE 100 price index"
        elif ticker.endswith((".SS", ".SZ")):
            symbol, label = "000300.SS", "CSI 300 price index"
        elif ticker.endswith(".HK"):
            symbol, label = "^HSI", "Hang Seng price index"
        elif ticker.endswith("-USD") or quote_type == "CRYPTOCURRENCY":
            symbol, label = "BTC-USD", "Bitcoin market context"
        elif "." in ticker:
            symbol, label = None, "No regional benchmark configured"
        else:
            symbol, label = "SPY", "S&P 500 ETF (SPY)"
        benchmark, benchmark_currency = [], "Unknown"
        try:
            if symbol:
                comparison = self.factory(symbol)
                benchmark = self._rows(comparison.history(
                    period="1y", interval="1d", auto_adjust=True, actions=False,
                    timeout=10, raise_errors=True))
                benchmark_currency = (getattr(comparison, "history_metadata", {}) or {}).get(
                    "currency", "Unknown")
        except Exception:
            pass
        rows = self._rows(history)
        cutoff = (datetime.fromisoformat(rows[-1]["date"]) - timedelta(
            days={"1M": 31, "3M": 93, "1Y": 366}[window])).date().isoformat()
        rows = [row for row in rows if row["date"] >= cutoff]
        aligned = aligned_rows(rows, benchmark)
        currency = metadata.get("currency", "Unknown quote unit")
        context = ("Futures exposure can differ from spot oil because of contract rolls, fees and "
                   "currency moves. A continuous futures series is product context, not a total-return benchmark."
                   if asset.asset_type == AssetType.COMMODITY_ETP else
                   "Relative returns use common trading dates and each series' own quote currency; "
                   "they do not include currency conversion. Index comparisons may exclude dividends.")
        value = {"ticker": ticker, "currency": currency, "benchmark": label,
                 "benchmark_symbol": symbol, "benchmark_currency": benchmark_currency,
                 "rows": rows, "comparison": aligned, "source": "Yahoo Finance / yfinance",
                 "retrieved_at": datetime.now(timezone.utc).isoformat(),
                 "adjustment": "Split- and dividend-adjusted daily Close (auto_adjust=True).",
                 "context": context, "asset_type": asset.asset_type.value,
                 "comparison_status": "Available" if aligned else "Benchmark unavailable; no comparison calculated"}
        with self.lock:
            self.cache[key] = (time.monotonic(), value)
        return value

    def financial(self, ticker: str):
        adapter = YFinanceMarketDataAdapter(ticker_factory=self.factory)
        if asset_profile_for(ticker).asset_type == AssetType.COMMODITY_ETP:
            context = adapter.get_commodity_etp_context(ticker)
            return {"kind": "commodity", "context": asdict(context)}
        # A single company has no meaningful peer comparison without a configured comparator.
        if not peer_symbol(ticker):
            return {"kind": "unavailable", "message": "No verified peer mapping is configured for this ticker. Financial context, where available, is included with prepared report evidence."}
        return {"kind": "equity", "context": asdict(adapter.get_fundamental_context(ticker)),
                "peer": peer_symbol(ticker)}

    @staticmethod
    def _rows(frame):
        rows = []
        for index, row in frame.iterrows():
            close = float(row["Close"])
            volume = float(row.get("Volume", float("nan")))
            if _positive(close):
                rows.append({"date": index.date().isoformat(), "close": close,
                             "volume": int(volume) if math.isfinite(volume) and volume >= 0 else None})
        if not rows:
            raise ValueError("No usable daily prices were returned.")
        return sorted(rows, key=lambda row: row["date"])
