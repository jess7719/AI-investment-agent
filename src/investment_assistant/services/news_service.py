"""Combine, deduplicate and rank metadata from multiple news sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import ceil
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from investment_assistant.domain.news import (
    ContentClassification,
    NewsArticle,
    aliases_for,
)
from investment_assistant.ports.news import (
    InvalidTickerError,
    NewsSourceAdapter,
    NewsSourceError,
    NewsUnavailableError,
)

TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")
TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


@dataclass(frozen=True, slots=True)
class NewsReport:
    ticker: str
    articles: tuple[NewsArticle, ...]
    unavailable_sources: tuple[str, ...]
    warnings: tuple[str, ...]
    official_first: bool = False


class NewsService:
    def __init__(self, sources: list[NewsSourceAdapter]) -> None:
        self._sources = sources

    def get_recent(
        self,
        ticker: str,
        fetch_limit: int = 20,
        result_limit: int = 10,
        official_first: bool = False,
    ) -> NewsReport:
        normalized_ticker = ticker.strip().upper()
        if not TICKER_PATTERN.fullmatch(normalized_ticker):
            raise InvalidTickerError(
                "Ticker must contain only letters, numbers, '.', '-', '^' or '='."
            )

        unavailable = tuple(
            f"{source.name} - {source.unavailable_reason}"
            for source in self._sources
            if not source.enabled
        )
        enabled_sources = [source for source in self._sources if source.enabled]
        source_batches: list[list[NewsArticle]] = []
        warnings: list[str] = []

        per_source_limit = ceil(fetch_limit / len(enabled_sources)) if enabled_sources else 0
        for source in enabled_sources:
            try:
                source_batches.append(source.fetch_recent(normalized_ticker, per_source_limit))
            except NewsSourceError as error:
                warnings.append(str(error))

        if enabled_sources and len(warnings) == len(enabled_sources) and not source_batches:
            raise NewsUnavailableError("; ".join(warnings))

        # Round-robin prevents the first adapter from crowding out later sources.
        candidates: list[NewsArticle] = []
        for position in range(fetch_limit):
            for batch in source_batches:
                if position < len(batch):
                    candidates.append(batch[position])
                    if len(candidates) == fetch_limit:
                        break
            if len(candidates) == fetch_limit:
                break

        scored = [self._score(article, official_first) for article in candidates]
        unique = self._deduplicate(scored)
        ranking_key = self._official_first_key if official_first else self._standard_key
        ranked = sorted(unique, key=ranking_key, reverse=True)[:result_limit]
        return NewsReport(
            normalized_ticker,
            tuple(ranked),
            unavailable,
            tuple(warnings),
            official_first,
        )

    @staticmethod
    def _score(article: NewsArticle, official_first: bool = False) -> NewsArticle:
        headline = article.headline.lower()
        ticker_pattern = rf"(?<![a-z0-9]){re.escape(article.ticker.lower())}(?![a-z0-9])"
        relevance = 35
        if re.search(ticker_pattern, headline):
            relevance += 35
        if any(alias.lower() in headline for alias in aliases_for(article.ticker)):
            relevance += 30
        importance = 0
        keyword_groups = (
            (30, ("earnings", "results", "revenue", "guidance", "outlook")),
            (30, ("acquisition", "merger", "buyout", "takeover")),
            (25, ("investigation", "lawsuit", "regulator", "antitrust", "ban")),
            (20, ("chief executive", " ceo ", "resigns", "appointed")),
            (20, ("dividend", "buyback", "offering", "capital raise")),
            (12, ("upgrade", "downgrade", "price target", "analyst")),
            (10, ("launch", "chip", "partnership", "contract")),
            (30, ("opec", "production cut", "production increase", "supply disruption")),
            (25, ("oil demand", "crude inventories", "eia inventory", "oil exports")),
            (25, ("sanctions", "shipping disruption", "strait of hormuz", "red sea")),
            (20, ("contango", "backwardation", "brent futures", "roll yield")),
            (15, ("china demand", "us crude production", "recession")),
        )
        padded_headline = f" {headline} "
        for points, keywords in keyword_groups:
            if any(keyword in padded_headline for keyword in keywords):
                importance += points

        if article.filing_form:
            importance = max(importance, filing_importance(article))

        relevance = min(relevance, 100)
        importance = min(importance, 100)
        confidence = source_confidence(article.content_classification)
        recency = recency_score(article.published_at)
        ranking = balanced_ranking_score(
            relevance,
            importance,
            confidence,
            recency,
            official_first,
        )

        return replace(
            article,
            relevance_score=relevance,
            importance_score=importance,
            source_confidence_score=confidence,
            recency_score=recency,
            ranking_score=ranking,
        )

    @staticmethod
    def _standard_key(article: NewsArticle) -> tuple[float, int, int, int, datetime]:
        return (
            article.ranking_score,
            article.importance_score,
            article.relevance_score,
            article.source_confidence_score,
            article.published_at,
        )

    @staticmethod
    def _official_first_key(article: NewsArticle) -> tuple[float, int, int, int, datetime]:
        return (
            article.ranking_score,
            article.importance_score,
            article.relevance_score,
            article.source_confidence_score,
            article.published_at,
        )

    @staticmethod
    def _deduplicate(articles: list[NewsArticle]) -> list[NewsArticle]:
        unique: dict[str, NewsArticle] = {}
        seen_headlines: dict[str, str] = {}
        for article in articles:
            url_key = canonicalize_url(article.url)
            headline_key = normalize_article_headline(article)
            existing_key = url_key if url_key in unique else seen_headlines.get(headline_key)
            if existing_key is None:
                unique[url_key] = article
                seen_headlines[headline_key] = url_key
                continue
            existing = unique[existing_key]
            if (
                article.ranking_score,
                article.source_confidence_score,
                article.relevance_score,
                article.importance_score,
            ) > (
                existing.ranking_score,
                existing.source_confidence_score,
                existing.relevance_score,
                existing.importance_score,
            ):
                unique[existing_key] = article
        return list(unique.values())


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
        )
    )
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, "")
    )


def normalize_headline(headline: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", headline.lower()).split())


def normalize_article_headline(article: NewsArticle) -> str:
    headline = article.headline
    publisher_suffix = re.compile(rf"\s+-\s+{re.escape(article.publisher)}\s*$", re.IGNORECASE)
    return normalize_headline(publisher_suffix.sub("", headline))


def source_confidence(classification: ContentClassification) -> int:
    return {
        ContentClassification.REGULATORY_FILING: 100,
        ContentClassification.OFFICIAL_COMPANY_RELEASE: 95,
        ContentClassification.FACTUAL_FINANCIAL_REPORTING: 80,
        ContentClassification.ANALYST_COMMENTARY: 60,
        ContentClassification.OPINION: 35,
        ContentClassification.AGGREGATION_OR_UNKNOWN: 20,
    }[classification]


def filing_importance(article: NewsArticle) -> int:
    """Potential importance from filing type and structured metadata, not credibility."""

    form = (article.filing_form or "").upper()
    if form in {"10-K", "10-Q"}:
        return 65
    if form in {"S-1", "S-1/A", "424B4"}:
        return 60
    if form in {"4", "4/A"}:
        return form4_importance(article)
    if form in {"3", "3/A"}:
        return 5
    if form == "8-K":
        searchable = " ".join(
            (article.headline, article.filing_description or "", *article.filing_items)
        ).lower()
        if "2.02" in article.filing_items or any(
            word in searchable for word in ("earnings", "results", "revenue", "guidance")
        ):
            return 65
        if "2.01" in article.filing_items or any(
            word in searchable
            for word in ("acquisition", "merger", "takeover", "major transaction")
        ):
            return 60
        if any(item in article.filing_items for item in ("2.03", "3.02")) or any(
            word in searchable
            for word in ("financing", "offering", "debt", "bond", "capital raise")
        ):
            return 55
        if "1.01" in article.filing_items:
            return 35
        return 20
    return 5


def form4_importance(article: NewsArticle) -> int:
    """Open-market trades can be medium importance; other/unknown Form 4s stay low."""

    open_market = [
        transaction
        for transaction in article.insider_transactions
        if transaction.transaction_code in {"P", "S"}
    ]
    if not open_market:
        return 8
    meaningful = any(
        (transaction.shares is not None and transaction.shares >= 10_000)
        or (
            transaction.shares is not None
            and transaction.price_per_share is not None
            and transaction.shares * transaction.price_per_share >= 100_000
        )
        for transaction in open_market
    )
    return 35 if meaningful else 15


def recency_score(published_at: datetime) -> int:
    age_seconds = (
        datetime.now(timezone.utc) - published_at.astimezone(timezone.utc)
    ).total_seconds()
    age_days = max(age_seconds, 0) / 86_400
    if age_days <= 1:
        return 100
    if age_days <= 3:
        return 85
    if age_days <= 7:
        return 70
    if age_days <= 14:
        return 50
    if age_days <= 30:
        return 30
    if age_days <= 90:
        return 10
    return 0


def balanced_ranking_score(
    relevance: int,
    importance: int,
    confidence: int,
    recency: int,
    official_first: bool,
) -> float:
    if official_first:
        weights = (0.25, 0.35, 0.30, 0.10)
    else:
        weights = (0.35, 0.35, 0.20, 0.10)
    return round(
        relevance * weights[0]
        + importance * weights[1]
        + confidence * weights[2]
        + recency * weights[3],
        1,
    )
