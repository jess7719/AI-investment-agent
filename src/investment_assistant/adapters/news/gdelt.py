"""Public GDELT DOC 2.0 article-metadata adapter."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from json import JSONDecodeError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from investment_assistant.domain.news import (
    NewsArticle,
    aliases_for,
    classify_content,
)
from investment_assistant.ports.news import NewsSourceError

JsonFetcher = Callable[[str, float], bytes]


class GDELTNewsAdapter:
    """Fetch public metadata without opening or scraping destination articles."""

    name = "GDELT DOC 2.0"
    enabled = True
    unavailable_reason = None
    endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, fetcher: JsonFetcher | None = None, timeout: float = 15.0) -> None:
        self._fetcher = fetcher or self._fetch_json
        self._timeout = timeout

    def fetch_recent(self, ticker: str, limit: int) -> list[NewsArticle]:
        aliases = aliases_for(ticker)
        terms = [ticker, *(f'"{alias}"' for alias in aliases)]
        query = f"({' OR '.join(terms)})" if len(terms) > 1 else ticker
        parameters = urlencode(
            {
                "query": query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": min(max(limit, 1), 20),
                "timespan": "7d",
                "sort": "datedesc",
            }
        )
        request_url = f"{self.endpoint}?{parameters}"

        try:
            payload = json.loads(self._fetcher(request_url, self._timeout))
        except (OSError, TimeoutError, JSONDecodeError, UnicodeDecodeError) as error:
            raise NewsSourceError(
                f"{self.name} request failed ({type(error).__name__}). "
                "Check the network connection or try again later."
            ) from error

        raw_articles = payload.get("articles", []) if isinstance(payload, dict) else []
        if not isinstance(raw_articles, list):
            raise NewsSourceError(f"{self.name} returned an unexpected response.")

        articles: list[NewsArticle] = []
        for raw in raw_articles[:limit]:
            parsed = self._parse_article(ticker, raw)
            if parsed is not None:
                articles.append(parsed)
        return articles

    @staticmethod
    def _fetch_json(url: str, timeout: float) -> bytes:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "personal-investment-assistant/0.1 (metadata research tool)",
            },
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
            return response.read()

    @staticmethod
    def _parse_article(ticker: str, raw: object) -> NewsArticle | None:
        if not isinstance(raw, dict):
            return None
        headline = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not headline or not url.startswith(("https://", "http://")):
            return None

        domain = str(raw.get("domain") or urlparse(url).hostname or "").lower()
        publisher = domain.removeprefix("www.") or "Unknown publisher"
        published_at = GDELTNewsAdapter._parse_timestamp(str(raw.get("seendate") or ""))
        if published_at is None:
            return None

        return NewsArticle(
            ticker=ticker,
            headline=headline,
            publisher=publisher,
            published_at=published_at,
            url=url,
            summary=None,
            content_classification=classify_content(ticker, domain, headline),
        )

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
