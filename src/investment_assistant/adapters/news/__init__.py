"""News metadata adapters; none of them downloads article body text."""

from investment_assistant.adapters.news.gdelt import GDELTNewsAdapter
from investment_assistant.adapters.news.google_news_rss import GoogleNewsRSSAdapter
from investment_assistant.adapters.news.investor_relations import InvestorRelationsNewsAdapter
from investment_assistant.adapters.news.sec_edgar import SECEdgarNewsAdapter
from investment_assistant.adapters.news.subscription_placeholders import (
    FTNewsAdapter,
    UniversityNewsDatabaseAdapter,
)

__all__ = [
    "FTNewsAdapter",
    "GDELTNewsAdapter",
    "GoogleNewsRSSAdapter",
    "InvestorRelationsNewsAdapter",
    "SECEdgarNewsAdapter",
    "UniversityNewsDatabaseAdapter",
]
