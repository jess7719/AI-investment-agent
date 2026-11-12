from datetime import datetime, timezone
from pathlib import Path

from investment_assistant.adapters.content_cache import ContentCache
from investment_assistant.adapters.public_article_content import PublicArticleContentAdapter
from investment_assistant.domain.evidence import EvidenceDepth
from investment_assistant.domain.news import ContentClassification, NewsArticle


def article(summary: str | None = None) -> NewsArticle:
    return NewsArticle(
        ticker="SPCX",
        headline="SpaceX announces an operational development",
        publisher="Example News",
        published_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        url="https://example.com/article",
        summary=summary,
        content_classification=ContentClassification.FACTUAL_FINANCIAL_REPORTING,
    )


def test_extracts_public_body_and_reuses_bounded_cache(tmp_path: Path) -> None:
    paragraph = " ".join(["Material operational fact for investors."] * 100)
    html = f"<html><body><article><h1>Development</h1><p>{paragraph}</p></article></body></html>".encode()
    calls = 0

    def fetcher(url: str, user_agent: str) -> tuple[bytes, str, str]:
        nonlocal calls
        calls += 1
        return html, url, "text/html"

    adapter = PublicArticleContentAdapter(
        "ResearchBot",
        ContentCache(tmp_path),
        fetcher=fetcher,
        resolver=lambda url, user_agent: url,
        robots_checker=lambda url, user_agent: True,
    )

    first = adapter.enrich(article())
    second = adapter.enrich(article())

    assert calls == 1
    assert first.evidence_depth is EvidenceDepth.FULL_PUBLIC_ARTICLE_EXTRACT
    assert len(first.content or "") <= 3000
    assert second.cache_hit is True


def test_respects_robots_and_does_not_fetch_page(tmp_path: Path) -> None:
    def forbidden(url: str, user_agent: str) -> tuple[bytes, str, str]:
        raise AssertionError("robots denial must prevent the article request")

    adapter = PublicArticleContentAdapter(
        "ResearchBot",
        ContentCache(tmp_path),
        fetcher=forbidden,
        resolver=lambda url, user_agent: url,
        robots_checker=lambda url, user_agent: False,
    )

    evidence = adapter.enrich(article("Publisher summary"))

    assert evidence.evidence_depth is EvidenceDepth.SOURCE_PROVIDED_SUMMARY
    assert evidence.content == "Publisher summary"
    assert "robots" in (evidence.access_note or "")


def test_paywall_marker_uses_public_description_not_body(tmp_path: Path) -> None:
    html = b"""<html><head><meta property="og:description" content="Public description."></head>
    <body>Subscribe to continue. Secret article body text.</body></html>"""
    adapter = PublicArticleContentAdapter(
        "ResearchBot",
        ContentCache(tmp_path),
        fetcher=lambda url, user_agent: (html, url, "text/html"),
        resolver=lambda url, user_agent: url,
        robots_checker=lambda url, user_agent: True,
    )

    evidence = adapter.enrich(article())

    assert evidence.evidence_depth is EvidenceDepth.PUBLIC_METADATA_DESCRIPTION
    assert evidence.content == "Public description."
    assert "Secret article body" not in (evidence.content or "")
