"""Verified company investor-relations release-list metadata adapter."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, time, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from investment_assistant.domain.news import (
    ContentClassification,
    NewsArticle,
    company_profile_for,
    domain_matches,
)
from investment_assistant.ports.news import NewsSourceError

HtmlFetcher = Callable[[str, float], bytes]
DATE_PATTERN = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s+\d{4}$"
)


class InvestorRelationsNewsAdapter:
    """Parse only release metadata from a configured, verified IR listing page."""

    name = "Verified investor-relations releases"
    enabled = True
    unavailable_reason = None

    def __init__(self, fetcher: HtmlFetcher | None = None, timeout: float = 15.0) -> None:
        self._fetcher = fetcher or self._fetch_html
        self._timeout = timeout

    def fetch_recent(self, ticker: str, limit: int) -> list[NewsArticle]:
        profile = company_profile_for(ticker)
        if (
            profile is None
            or profile.verified_ir_domain is None
            or profile.ir_updates_url is None
        ):
            return []
        if not domain_matches(
            urlparse(profile.ir_updates_url).hostname or "", profile.verified_ir_domain
        ):
            raise NewsSourceError(f"{self.name} rejected an unverified listing domain.")

        try:
            html = self._fetcher(profile.ir_updates_url, self._timeout).decode(
                "utf-8", errors="replace"
            )
        except (OSError, TimeoutError) as error:
            raise NewsSourceError(
                f"{self.name} request failed ({type(error).__name__}). "
                "Check the network connection or try again later."
            ) from error

        parser = _ReleaseListParser()
        parser.feed(html)
        articles: list[NewsArticle] = []
        seen_urls: set[str] = set()
        for release_date, title, href in parser.releases:
            official_url = urljoin(profile.ir_updates_url, href)
            if not domain_matches(
                urlparse(official_url).hostname or "", profile.verified_ir_domain
            ):
                continue
            if official_url in seen_urls:
                continue
            seen_urls.add(official_url)
            articles.append(
                NewsArticle(
                    ticker=profile.ticker,
                    headline=title,
                    publisher=profile.legal_name,
                    published_at=datetime.combine(release_date, time(), timezone.utc),
                    url=official_url,
                    summary=None,
                    content_classification=ContentClassification.OFFICIAL_COMPANY_RELEASE,
                )
            )
            if len(articles) == limit:
                break
        return articles

    @staticmethod
    def _fetch_html(url: str, timeout: float) -> bytes:
        request = Request(
            url,
            headers={
                "Accept": "text/html",
                "User-Agent": "personal-investment-assistant/0.1 (metadata research tool)",
            },
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - verified HTTPS
            return response.read()


class _ReleaseListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.releases: list[tuple[date, str, str]] = []
        self._last_date: date | None = None
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "time" and attributes.get("datetime"):
            self._set_iso_date(attributes["datetime"] or "")
        href = attributes.get("href") if tag == "a" else None
        if href and "/updates/releases-details/" in href:
            self._active_href = href
            self._active_text = []

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if DATE_PATTERN.fullmatch(normalized):
            try:
                self._last_date = datetime.strptime(normalized, "%B %d, %Y").date()
            except ValueError:
                pass
        if self._active_href and normalized:
            self._active_text.append(normalized)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._active_href is None:
            return
        title = " ".join(self._active_text).strip()
        if (
            self._last_date is not None
            and title
            and title.lower() not in {"learn more", "download", "view"}
        ):
            self.releases.append((self._last_date, title, self._active_href))
        self._active_href = None
        self._active_text = []

    def _set_iso_date(self, value: str) -> None:
        try:
            self._last_date = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
