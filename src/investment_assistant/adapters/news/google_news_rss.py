"""Google News public RSS metadata adapter."""

from __future__ import annotations

from collections.abc import Callable
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from investment_assistant.domain.news import NewsArticle, aliases_for, classify_content
from investment_assistant.ports.news import NewsSourceError

XmlFetcher = Callable[[str, float], bytes]


class GoogleNewsRSSAdapter:
    """Read openly accessible RSS metadata without opening destination articles."""

    name = "Google News RSS"
    enabled = True
    unavailable_reason = None
    endpoint = "https://news.google.com/rss/search"

    def __init__(self, fetcher: XmlFetcher | None = None, timeout: float = 15.0) -> None:
        self._fetcher = fetcher or self._fetch_xml
        self._timeout = timeout

    def fetch_recent(self, ticker: str, limit: int) -> list[NewsArticle]:
        terms = [ticker, *aliases_for(ticker)]
        parameters = urlencode(
            {
                "q": " OR ".join(terms),
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            }
        )
        request_url = f"{self.endpoint}?{parameters}"
        try:
            root = ElementTree.fromstring(self._fetcher(request_url, self._timeout))
        except (OSError, TimeoutError, ElementTree.ParseError) as error:
            raise NewsSourceError(
                f"{self.name} request failed ({type(error).__name__}). "
                "Check the network connection or try again later."
            ) from error

        articles: list[NewsArticle] = []
        for item in root.findall("./channel/item")[:limit]:
            parsed = self._parse_item(ticker, item)
            if parsed is not None:
                articles.append(parsed)
        return articles

    @staticmethod
    def _fetch_xml(url: str, timeout: float) -> bytes:
        request = Request(
            url,
            headers={
                "Accept": "application/rss+xml, application/xml",
                "User-Agent": "personal-investment-assistant/0.1 (metadata research tool)",
            },
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
            return response.read()

    @staticmethod
    def _parse_item(ticker: str, item: ElementTree.Element) -> NewsArticle | None:
        headline = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        published_text = (item.findtext("pubDate") or "").strip()
        source_element = item.find("source")
        publisher = (
            (source_element.text or "").strip()
            if source_element is not None
            else "Unknown publisher"
        )
        source_url = source_element.get("url", "") if source_element is not None else ""
        domain = (urlparse(source_url).hostname or "").lower()

        if not headline or not url.startswith("https://") or not published_text:
            return None
        try:
            published_at = parsedate_to_datetime(published_text)
        except (TypeError, ValueError):
            return None
        if published_at.tzinfo is None:
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
