"""Reviewed public metadata plus bounded Google News RSS; never opens article URLs."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree

from investment_assistant.adapters.news.google_news_rss import GoogleNewsRSSAdapter
from investment_assistant.domain.historical_context import EventCategory, HistoricalSource


CATEGORY_TERMS = {
    "tariffs_trade": ("tariff", "trade war", "trade policy"),
    "monetary_policy": ("federal reserve", "fed rate", "fomc", "central bank"),
    "interest_rates": ("treasury", "bond yield", "interest rate"),
    "inflation": ("inflation", "consumer price", "producer price", "pce"),
    "employment": ("payroll", "employment", "jobless", "hiring"),
    "economic_growth": ("gdp", "growth", "consumer spending", "recession"),
    "geopolitics": ("war", "iran", "invasion", "conflict"),
    "earnings": ("earnings", "quarterly profit"),
    "technology": ("nvidia", "semiconductor", "technology", "artificial intelligence"),
    "credit_stress": ("credit stress", "default", "credit spread"),
    "banking": ("banking", "bank failure", "bank deposit"),
    "energy": ("oil", "energy", "opec", "gas price"),
    "commodity_shock": ("commodity", "copper", "wheat"),
    "fiscal_policy": ("fiscal", "budget", "government spending"),
    "regulation": ("regulat", "antitrust", "sec rule"),
    "market_structure": ("liquidity", "trading halt", "margin call"),
    "sector_rotation": ("sector rotation", "value stocks", "growth stocks"),
}


def categorize(headline):
    return next((EventCategory(k) for k, words in CATEGORY_TERMS.items()
                 if any(w in headline.lower() for w in words)), EventCategory.OTHER)


def source_class(domain):
    domain = domain.lower().removeprefix("www.")
    if domain in {"bea.gov", "bls.gov", "census.gov"}:
        return "official_data"
    if domain.endswith(".gov"):
        return "official_policy"
    if domain in {"reuters.com", "apnews.com"}:
        return "wire"
    if domain in {"cnbc.com", "ft.com", "wsj.com", "bloomberg.com", "finance.yahoo.com"}:
        return "financial_news"
    return "aggregation"


class PublicHistoricalMetadataAdapter:
    def __init__(self, rss=None, catalogue=None):
        self.rss = rss or GoogleNewsRSSAdapter(timeout=8)
        self.catalogue = catalogue or Path(__file__).with_name("historical_sources.json")

    def curated(self, start, end):
        try:
            rows = json.loads(self.catalogue.read_text(encoding="utf-8"))
            return tuple(s for row in rows if
                         start <= (s := HistoricalSource.model_validate(row)).published_at.date()
                         <= end)
        except (OSError, ValueError):
            return ()

    def fetch_window(self, start, end):
        # The remote endpoint returns metadata only. Its date query is also rechecked by service.
        curated = self.curated(start, end)
        parameters = urlencode({
            "q": '("US stocks" OR Treasury OR "Federal Reserve" OR inflation OR tariffs) '
                 f'after:{start - timedelta(days=1)} before:{end + timedelta(days=1)}',
            "hl": "en-US", "gl": "US", "ceid": "US:en",
        })
        try:
            root = ElementTree.fromstring(self.rss._fetcher(
                f"{self.rss.endpoint}?{parameters}", self.rss._timeout))
        except (OSError, TimeoutError, ElementTree.ParseError):
            # Curated evidence remains useful when the public search endpoint is unavailable.
            return curated
        sources = list(curated)
        for item in root.findall("./channel/item")[:60]:
            article = self.rss._parse_item("SPY", item)
            if article is None:
                continue
            source = item.find("source")
            domain = urlparse(source.get("url", "") if source is not None else "").hostname or ""
            try:
                sources.append(HistoricalSource(
                    reference=hashlib.sha256(article.url.encode()).hexdigest()[:16],
                    headline=article.headline[:350], publisher=article.publisher[:150],
                    published_at=article.published_at, url=article.url,
                    source_classification=source_class(domain),
                    category=categorize(article.headline), entities=("US market",),
                ))
            except ValueError:
                continue
        return tuple(sources)

    def offline_window(self, start, end):
        return self.curated(start, end)


def conservative_date_timestamp(day):
    """Date-only source records are conservatively available at the end of their UTC day."""
    return datetime.fromisoformat(day).replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc)
