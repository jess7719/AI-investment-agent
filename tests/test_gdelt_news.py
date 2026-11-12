import json
from urllib.parse import parse_qs, urlsplit

import pytest

from investment_assistant.adapters.news.gdelt import GDELTNewsAdapter
from investment_assistant.domain.news import ContentClassification
from investment_assistant.ports.news import NewsSourceError


def test_parses_public_article_metadata_without_body_text() -> None:
    requested: dict[str, object] = {}
    payload = {
        "articles": [
            {
                "title": "SpaceX announces an investor update",
                "url": "https://ir.spacex.com/updates/releases-details/example",
                "domain": "ir.spacex.com",
                "seendate": "20260713T120000Z",
            }
        ]
    }

    def fake_fetcher(url: str, timeout: float) -> bytes:
        requested.update(url=url, timeout=timeout)
        return json.dumps(payload).encode()

    articles = GDELTNewsAdapter(fetcher=fake_fetcher).fetch_recent("SPCX", 20)

    assert len(articles) == 1
    assert articles[0].headline == "SpaceX announces an investor update"
    assert articles[0].publisher == "ir.spacex.com"
    assert articles[0].summary is None
    assert (
        articles[0].content_classification
        is ContentClassification.OFFICIAL_COMPANY_RELEASE
    )
    assert articles[0].published_at.isoformat() == "2026-07-13T12:00:00+00:00"
    query = parse_qs(urlsplit(str(requested["url"])).query)
    assert query["maxrecords"] == ["20"]
    assert query["query"] == [
        '(SPCX OR "spacex" OR "space exploration technologies corp.")'
    ]
    assert requested["timeout"] == 15.0


def test_skips_articles_missing_required_metadata() -> None:
    payload = {
        "articles": [
            {"title": "Missing URL", "seendate": "20260713T120000Z"},
            {"url": "https://example.com/story", "seendate": "20260713T120000Z"},
            {"title": "Missing date", "url": "https://example.com/story"},
        ]
    }
    adapter = GDELTNewsAdapter(fetcher=lambda url, timeout: json.dumps(payload).encode())

    assert adapter.fetch_recent("NVDA", 20) == []


def test_translates_network_failure_to_source_error() -> None:
    def failing_fetcher(url: str, timeout: float) -> bytes:
        raise TimeoutError("timed out")

    with pytest.raises(NewsSourceError, match="request failed"):
        GDELTNewsAdapter(fetcher=failing_fetcher).fetch_recent("NVDA", 20)
