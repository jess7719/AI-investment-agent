"""Independent replaceable evidence capabilities; no vendor or interpretation logic."""

from datetime import date
from typing import Protocol

from investment_assistant.domain.regime import DailyObservation, MacroObservation


class BroadMarketDataProvider(Protocol):
    def market_history(self, symbol: str, start: date, end: date) -> tuple[DailyObservation, ...]:
        """Daily market facts, inclusive endpoints; also supports target equity histories."""
        ...


class VolatilityDataProvider(Protocol):
    def volatility_history(
        self, symbol: str, start: date, end: date,
    ) -> tuple[DailyObservation, ...]: ...


class RatesDataProvider(Protocol):
    def rates_history(self, symbol: str, start: date, end: date) -> tuple[DailyObservation, ...]:
        """Yield levels in percentage points, not a bond-price series."""
        ...


class MacroDataProvider(Protocol):
    def macro_history(self, dimension: str, as_of: date) -> tuple[MacroObservation, ...]:
        """Official, publication-dated vintages only; revised future vintages must be excluded."""
        ...
