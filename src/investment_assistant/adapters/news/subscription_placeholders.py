"""Deliberately disabled adapters for licensed subscription sources."""

from __future__ import annotations

from investment_assistant.domain.news import NewsArticle
from investment_assistant.ports.news import SubscriptionAdapterUnavailable


class _DisabledSubscriptionAdapter:
    enabled = False
    unavailable_reason: str

    def fetch_recent(self, ticker: str, limit: int) -> list[NewsArticle]:
        del ticker, limit
        raise SubscriptionAdapterUnavailable(self.unavailable_reason)


class FTNewsAdapter(_DisabledSubscriptionAdapter):
    name = "FT API"
    unavailable_reason = (
        "disabled: legitimate API credentials and confirmation of programmatic subscription "
        "rights are required"
    )


class UniversityNewsDatabaseAdapter(_DisabledSubscriptionAdapter):
    name = "University news database API"
    unavailable_reason = (
        "disabled: university API credentials and confirmation that the licence permits "
        "programmatic access are required"
    )
