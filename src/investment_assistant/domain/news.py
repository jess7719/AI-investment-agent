"""Company configuration and legally collected news metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class ContentClassification(StrEnum):
    REGULATORY_FILING = "regulatory filing"
    OFFICIAL_COMPANY_RELEASE = "official company release"
    FACTUAL_FINANCIAL_REPORTING = "factual financial reporting"
    ANALYST_COMMENTARY = "analyst commentary"
    OPINION = "opinion"
    AGGREGATION_OR_UNKNOWN = "aggregation or unknown"


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    ticker: str
    legal_name: str
    aliases: tuple[str, ...]
    sec_cik: str | None = None
    verified_ir_domain: str | None = None
    ir_updates_url: str | None = None

    @property
    def padded_cik(self) -> str | None:
        return self.sec_cik.zfill(10) if self.sec_cik else None


@dataclass(frozen=True, slots=True)
class InsiderTransaction:
    """Structured ownership fields reported by the filer; no sentiment is inferred."""

    reporting_person: str | None
    transaction_date: date | None
    transaction_code: str | None
    transaction_type: str
    shares: Decimal | None
    price_per_share: Decimal | None
    relationship_to_issuer: str | None = None
    ownership_after: Decimal | None = None


@dataclass(frozen=True, slots=True)
class NewsArticle:
    """Metadata only; filing documents and article body text are never stored."""

    ticker: str
    headline: str
    publisher: str
    published_at: datetime
    url: str
    summary: str | None
    content_classification: ContentClassification
    relevance_score: int = 0
    importance_score: int = 0
    source_confidence_score: int = 0
    recency_score: int = 0
    ranking_score: float = 0.0
    filing_form: str | None = None
    filing_description: str | None = None
    filing_items: tuple[str, ...] = ()
    insider_transactions: tuple[InsiderTransaction, ...] = ()


COMPANY_PROFILES: dict[str, CompanyProfile] = {
    "BRNT.L": CompanyProfile(
        "BRNT.L",
        "WisdomTree Brent Crude Oil",
        (
            "brent crude",
            "brent oil",
            "opec+",
            "oil demand",
            "oil inventories",
        ),
    ),
    "NVDA": CompanyProfile("NVDA", "NVIDIA Corporation", ("nvidia",)),
    "MSFT": CompanyProfile("MSFT", "Microsoft Corporation", ("microsoft",)),
    "SHOP": CompanyProfile("SHOP", "Shopify Inc.", ("shopify",)),
    "SPCX": CompanyProfile(
        ticker="SPCX",
        legal_name="Space Exploration Technologies Corp.",
        aliases=("spacex", "space exploration technologies corp."),
        sec_cik="1181412",
        verified_ir_domain="ir.spacex.com",
        ir_updates_url="https://ir.spacex.com/updates/",
    ),
}

FACTUAL_DOMAINS = {
    "reuters.com",
    "bloomberg.com",
    "cnbc.com",
    "ft.com",
    "wsj.com",
    "barrons.com",
    "marketwatch.com",
    "finance.yahoo.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "theguardian.com",
    "nytimes.com",
    "washingtonpost.com",
    "theglobeandmail.com",
}
ANALYST_DOMAINS = {
    "tipranks.com",
    "marketbeat.com",
    "investing.com",
    "benzinga.com",
    "thestreet.com",
    "trefis.com",
}
OPINION_DOMAINS = {
    "seekingalpha.com",
    "fool.com",
    "investorplace.com",
    "247wallst.com",
    "stocktwits.com",
}
OPINION_PHRASES = (
    "here's why",
    "here’s why",
    "prediction:",
    "should you",
    "shouldn't",
    "best stock",
    "buy instead",
    "buy now",
    "sell now",
    "i think",
    "could turn",
    "worth this much",
    "underestimates",
    "biggest risk",
    "real price",
    "target implies",
)
ANALYST_PHRASES = (
    "price target",
    "upgrade",
    "downgrade",
    "analyst",
    "buy rating",
    "strong buy",
    "initiates coverage",
    "reiterates",
    "cramer",
)


def company_profile_for(ticker: str) -> CompanyProfile | None:
    return COMPANY_PROFILES.get(ticker.upper())


def aliases_for(ticker: str) -> tuple[str, ...]:
    profile = company_profile_for(ticker)
    return profile.aliases if profile else ()


def domain_matches(domain: str, expected_domain: str) -> bool:
    normalized = domain.lower().removeprefix("www.")
    expected = expected_domain.lower().removeprefix("www.")
    return normalized == expected or normalized.endswith(f".{expected}")


def classify_content(ticker: str, domain: str, headline: str) -> ContentClassification:
    normalized_domain = domain.lower().removeprefix("www.")
    normalized_headline = headline.lower()
    profile = company_profile_for(ticker)

    if normalized_domain == "sec.gov" or normalized_domain.endswith(".sec.gov"):
        return ContentClassification.REGULATORY_FILING
    if profile and profile.verified_ir_domain and domain_matches(
        normalized_domain, profile.verified_ir_domain
    ):
        return ContentClassification.OFFICIAL_COMPANY_RELEASE
    if any(phrase in normalized_headline for phrase in OPINION_PHRASES) or any(
        domain_matches(normalized_domain, known) for known in OPINION_DOMAINS
    ):
        return ContentClassification.OPINION
    if any(phrase in normalized_headline for phrase in ANALYST_PHRASES) or any(
        domain_matches(normalized_domain, known) for known in ANALYST_DOMAINS
    ):
        return ContentClassification.ANALYST_COMMENTARY
    if any(domain_matches(normalized_domain, known) for known in FACTUAL_DOMAINS):
        return ContentClassification.FACTUAL_FINANCIAL_REPORTING
    return ContentClassification.AGGREGATION_OR_UNKNOWN
