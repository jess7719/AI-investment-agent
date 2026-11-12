"""Contracts and errors for replaceable news metadata sources."""

from __future__ import annotations

from typing import Protocol

from investment_assistant.domain.news import NewsArticle


class NewsError(RuntimeError):
    """Base error for the news feature."""


class InvalidTickerError(NewsError):
    """Raised when ticker syntax is unsafe or unsupported."""


class NewsSourceError(NewsError):
    """Raised when an enabled source cannot return usable metadata."""


class NewsUnavailableError(NewsError):
    """Raised when every enabled source fails."""


class SubscriptionAdapterUnavailable(NewsError):
    """Raised if code tries to call a disabled subscription adapter."""


class NewsSourceAdapter(Protocol):
    name: str
    enabled: bool
    unavailable_reason: str | None

    def fetch_recent(self, ticker: str, limit: int) -> list[NewsArticle]: ...
