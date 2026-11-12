"""Robots-aware extraction of bounded content from public news articles."""

from __future__ import annotations

import json
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.robotparser import RobotFileParser

import trafilatura
from bs4 import BeautifulSoup

from investment_assistant.adapters.content_cache import ContentCache
from investment_assistant.domain.evidence import EvidenceDepth, ExtractedEvidence
from investment_assistant.domain.news import ContentClassification, NewsArticle

MAX_ARTICLE_EXTRACT_CHARS = 3_000
MAX_DOWNLOAD_BYTES = 2_000_000
MIN_BODY_WORDS = 80
PAYWALL_MARKERS = (
    "subscribe to continue",
    "sign in to continue",
    "already a subscriber",
    "subscription required",
    "enable javascript and disable ad blocker",
    "captcha",
)

PageFetcher = Callable[[str, str], tuple[bytes, str, str]]
Resolver = Callable[[str, str], str | None]
RobotsChecker = Callable[[str, str], bool]


class PublicArticleContentAdapter:
    def __init__(
        self,
        user_agent: str,
        cache: ContentCache,
        fetcher: PageFetcher | None = None,
        resolver: Resolver | None = None,
        robots_checker: RobotsChecker | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._cache = cache
        self._fetcher = fetcher or fetch_page
        self._resolver = resolver or resolve_aggregator_url
        self._robots_checker = robots_checker or RobotsPolicy(cache).allowed

    def enrich(self, article: NewsArticle) -> ExtractedEvidence:
        cached = self._cache.read_json("article-extracts", article.url)
        if cached:
            return _from_cache(article, cached)

        resolved = self._resolver(article.url, self._user_agent) or article.url
        hostname = (urlparse(resolved).hostname or "").lower()
        if hostname.endswith("news.google.com"):
            return self._fallback(article, resolved, "Aggregator URL could not be resolved.")
        if not self._robots_checker(resolved, self._user_agent):
            return self._fallback(article, resolved, "Publisher robots policy disallows access.")
        try:
            html, final_url, content_type = self._fetcher(resolved, self._user_agent)
        except (OSError, TimeoutError):
            return self._fallback(article, resolved, "Public article request failed.")
        if "html" not in content_type.lower():
            return self._fallback(article, final_url, "Publisher response was not HTML.")

        evidence = extract_public_content(article, html, final_url)
        self._cache.write_json("article-extracts", article.url, _to_cache(evidence))
        return evidence

    def _fallback(
        self, article: NewsArticle, original_url: str, access_note: str
    ) -> ExtractedEvidence:
        if article.summary:
            return ExtractedEvidence(
                article,
                EvidenceDepth.SOURCE_PROVIDED_SUMMARY,
                _cap(article.summary),
                original_url,
                access_note=access_note,
            )
        return ExtractedEvidence(
            article,
            EvidenceDepth.HEADLINE_ONLY,
            None,
            original_url,
            access_note=access_note,
        )


class RobotsPolicy:
    def __init__(self, cache: ContentCache) -> None:
        self._cache = cache

    def allowed(self, url: str, user_agent: str) -> bool:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        robots_url = f"{origin}/robots.txt"
        cached = self._cache.read_bytes("robots", robots_url, ".txt")
        if cached is None:
            try:
                request = Request(robots_url, headers={"User-Agent": user_agent})
                with urlopen(request, timeout=10) as response:  # noqa: S310 - public URL
                    cached = response.read(500_000)
                self._cache.write_bytes("robots", robots_url, ".txt", cached)
            except HTTPError as error:
                return error.code in {404, 410}
            except (OSError, TimeoutError):
                return False
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(cached.decode("utf-8", errors="replace").splitlines())
        return parser.can_fetch(user_agent, url)


def extract_public_content(
    article: NewsArticle, html: bytes, original_url: str
) -> ExtractedEvidence:
    decoded = html.decode("utf-8", errors="replace")
    lower = decoded.lower()
    restricted = any(marker in lower for marker in PAYWALL_MARKERS)
    if not restricted:
        body = trafilatura.extract(
            decoded,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            output_format="txt",
        )
        if body and len(body.split()) >= MIN_BODY_WORDS:
            depth = (
                EvidenceDepth.OFFICIAL_COMPANY_RELEASE
                if article.content_classification
                is ContentClassification.OFFICIAL_COMPANY_RELEASE
                else EvidenceDepth.FULL_PUBLIC_ARTICLE_EXTRACT
            )
            return ExtractedEvidence(
                article,
                depth,
                _cap(body),
                original_url,
                cache_hit=False,
            )
    if article.summary:
        return ExtractedEvidence(
            article,
            EvidenceDepth.SOURCE_PROVIDED_SUMMARY,
            _cap(article.summary),
            original_url,
            access_note="Full public body was restricted or not extractable.",
        )
    metadata = _json_ld_content(decoded, allow_body=not restricted) or _open_graph_description(
        decoded
    )
    if metadata:
        content, is_body = metadata
        depth = (
            EvidenceDepth.FULL_PUBLIC_ARTICLE_EXTRACT
            if is_body
            else EvidenceDepth.PUBLIC_METADATA_DESCRIPTION
        )
        return ExtractedEvidence(
            article,
            depth,
            _cap(content),
            original_url,
            access_note=("Paywall/login marker detected." if restricted else None),
        )
    return ExtractedEvidence(
        article,
        EvidenceDepth.HEADLINE_ONLY,
        None,
        original_url,
        access_note=(
            "Paywall/login marker detected; no restricted content was extracted."
            if restricted
            else "No public body or metadata description was found."
        ),
    )


def resolve_aggregator_url(url: str, user_agent: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith("news.google.com"):
        return url
    opener = build_opener(_NoRedirect())
    current = url
    for _ in range(4):
        request = Request(current, method="HEAD", headers={"User-Agent": user_agent})
        try:
            response = opener.open(request, timeout=10)
            final = response.geturl()
            response.close()
            return final if not (urlparse(final).hostname or "").endswith("news.google.com") else None
        except HTTPError as error:
            location = error.headers.get("Location")
            if not location:
                return None
            current = urljoin(current, location)
            if not (urlparse(current).hostname or "").lower().endswith("news.google.com"):
                return current
        except (OSError, TimeoutError):
            return None
    return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        return None


def fetch_page(url: str, user_agent: str) -> tuple[bytes, str, str]:
    request = Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - robots-approved public URL
        return (
            response.read(MAX_DOWNLOAD_BYTES),
            response.geturl(),
            response.headers.get_content_type(),
        )


def _json_ld_content(html: str, allow_body: bool) -> tuple[str, bool] | None:
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        objects = data if isinstance(data, list) else [data]
        for value in objects:
            if not isinstance(value, dict):
                continue
            if allow_body and isinstance(value.get("articleBody"), str):
                return value["articleBody"], True
            if isinstance(value.get("description"), str):
                return value["description"], False
    return None


def _open_graph_description(html: str) -> tuple[str, bool] | None:
    parser = _MetadataParser()
    parser.feed(html)
    return (parser.description, False) if parser.description else None


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.description: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and values.get("property") in {"og:description", "description"}:
            self.description = values.get("content")
        if tag == "meta" and values.get("name") == "description" and not self.description:
            self.description = values.get("content")


def _to_cache(evidence: ExtractedEvidence) -> dict[str, object]:
    return {
        "evidence_depth": evidence.evidence_depth.value,
        "content": evidence.content,
        "original_url": evidence.original_url,
        "access_note": evidence.access_note,
    }


def _from_cache(article: NewsArticle, value: dict[str, object]) -> ExtractedEvidence:
    try:
        depth = EvidenceDepth(str(value["evidence_depth"]))
    except (KeyError, ValueError):
        depth = EvidenceDepth.HEADLINE_ONLY
    return ExtractedEvidence(
        article,
        depth,
        str(value["content"]) if value.get("content") else None,
        str(value.get("original_url") or article.url),
        cache_hit=True,
        access_note=str(value["access_note"]) if value.get("access_note") else None,
    )


def _cap(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= MAX_ARTICLE_EXTRACT_CHARS:
        return normalized
    return normalized[: MAX_ARTICLE_EXTRACT_CHARS - 1].rstrip() + "…"
