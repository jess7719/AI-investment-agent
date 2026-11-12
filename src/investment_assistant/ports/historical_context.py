"""Metadata retrieval only; historical explanation never requests article bodies."""

from datetime import date
from typing import Protocol

from investment_assistant.domain.historical_context import HistoricalSource


class HistoricalMetadataProvider(Protocol):
    def fetch_window(self, start: date, end: date) -> tuple[HistoricalSource, ...]: ...
