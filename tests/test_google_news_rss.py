from urllib.parse import parse_qs, urlsplit

import pytest

from investment_assistant.adapters.news import GoogleNewsRSSAdapter
from investment_assistant.domain.news import ContentClassification
from investment_assistant.ports.news import NewsSourceError


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Nvidia earnings guidance rises - Reuters</title>
      <link>https://news.google.com/rss/articles/example</link>
      <pubDate>Mon, 13 Jul 2026 12:30:00 GMT</pubDate>
      <source url="https://www.reuters.com">Reuters</source>
      <description>Metadata supplied by the feed but deliberately not stored.</description>
    </item>
  </channel>
</rss>
"""


def test_parses_rss_metadata_but_does_not_store_description() -> None:
    requested: dict[str, object] = {}

    def fake_fetcher(url: str, timeout: float) -> bytes:
        requested.update(url=url, timeout=timeout)
        return RSS

    articles = GoogleNewsRSSAdapter(fetcher=fake_fetcher).fetch_recent("NVDA", 10)

    assert len(articles) == 1
    assert articles[0].publisher == "Reuters"
    assert (
        articles[0].content_classification
        is ContentClassification.FACTUAL_FINANCIAL_REPORTING
    )
    assert articles[0].summary is None
    assert articles[0].published_at.isoformat() == "2026-07-13T12:30:00+00:00"
    query = parse_qs(urlsplit(str(requested["url"])).query)
    assert query["q"] == ["NVDA OR nvidia"]
    assert requested["timeout"] == 15.0


def test_translates_invalid_xml_to_source_error() -> None:
    adapter = GoogleNewsRSSAdapter(fetcher=lambda url, timeout: b"not XML")

    with pytest.raises(NewsSourceError, match="request failed"):
        adapter.fetch_recent("NVDA", 10)
