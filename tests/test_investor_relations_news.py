import pytest

from investment_assistant.adapters.news import InvestorRelationsNewsAdapter
from investment_assistant.domain.news import ContentClassification


IR_HTML = b"""
<html><body>
  <div class="release">
    <div class="date">June 22, 2026</div>
    <a href="/updates/releases-details/2026/inaugural-bond/default.aspx">
      Space Exploration Technologies Corp. Announces Inaugural Bond Issuance
    </a>
  </div>
  <div class="release">
    <time datetime="2026-06-15">June 15, 2026</time>
    <a href="https://ir.spacex.com/updates/releases-details/2026/ipo-close/default.aspx">
      Space Exploration Technologies Corp. Announces Closing of Initial Public Offering
    </a>
  </div>
  <div class="release">
    <div class="date">June 14, 2026</div>
    <a href="https://malicious.example/releases-details/not-official">Not official</a>
  </div>
</body></html>
"""


def test_parses_only_verified_ir_release_metadata() -> None:
    requested: dict[str, object] = {}

    def fake_fetcher(url: str, timeout: float) -> bytes:
        requested.update(url=url, timeout=timeout)
        return IR_HTML

    articles = InvestorRelationsNewsAdapter(fetcher=fake_fetcher).fetch_recent("SPCX", 10)

    assert len(articles) == 2
    assert articles[0].headline.endswith("Inaugural Bond Issuance")
    assert articles[0].published_at.isoformat() == "2026-06-22T00:00:00+00:00"
    assert articles[0].url.startswith("https://ir.spacex.com/")
    assert articles[0].summary is None
    assert (
        articles[0].content_classification
        is ContentClassification.OFFICIAL_COMPANY_RELEASE
    )
    assert requested == {"url": "https://ir.spacex.com/updates/", "timeout": 15.0}


def test_returns_no_releases_without_verified_ir_config() -> None:
    adapter = InvestorRelationsNewsAdapter(
        fetcher=lambda url, timeout: pytest.fail("must not fetch")
    )

    assert adapter.fetch_recent("UNKNOWN", 5) == []
