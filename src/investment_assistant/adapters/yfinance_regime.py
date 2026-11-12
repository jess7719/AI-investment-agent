"""Daily Yahoo facts via the existing yfinance dependency. No regime rules here."""

from datetime import date, datetime, timedelta, timezone
from math import isfinite

import yfinance as yf

from investment_assistant.adapters.yfinance_market_data import YFinanceMarketDataAdapter
from investment_assistant.domain.regime import DailyObservation


class YFinanceRegimeAdapter(YFinanceMarketDataAdapter):
    def __init__(self, ticker_factory=yf.Ticker, cache_dir=None):
        super().__init__(ticker_factory=ticker_factory, cache_dir=cache_dir)

    def _daily(self, symbol, start, end, unit):
        retrieved = datetime.now(timezone.utc)
        # Exclude today's possibly incomplete daily candle, even during a live session.
        cutoff = min(end, retrieved.date() - timedelta(days=1))
        if start > cutoff:
            return ()
        history = self._ticker_factory(symbol).history(
            start=start.isoformat(), end=(cutoff + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=False, back_adjust=False, actions=False,
            timeout=10, raise_errors=True,
        )
        if history is None or history.empty or "Close" not in history:
            return ()
        rows = []
        for timestamp, row in history.iterrows():
            day = timestamp.date()
            value = float(row["Close"])
            if not start <= day <= cutoff or not isfinite(value) or value <= 0:
                continue
            volume = float(row.get("Volume", float("nan")))
            rows.append(DailyObservation(
                date=day, available_on=day, value=value,
                volume=volume if isfinite(volume) and volume >= 0 else None,
                source="Yahoo Finance/yfinance daily close (third-party, not vintage-verified)",
                series=symbol, retrieved_at=retrieved, unit=unit,
            ))
        return tuple(rows)

    def market_history(self, symbol: str, start: date, end: date):
        return self._daily(symbol, start, end, "price")

    def volatility_history(self, symbol: str, start: date, end: date):
        return self._daily(symbol, start, end, "volatility index points")

    def rates_history(self, symbol: str, start: date, end: date):
        return self._daily(symbol, start, end, "yield percent")
