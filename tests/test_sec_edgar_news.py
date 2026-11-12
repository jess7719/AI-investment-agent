import json

import pytest

from investment_assistant.adapters.news import SECEdgarNewsAdapter
from investment_assistant.domain.news import ContentClassification
from investment_assistant.ports.news import NewsSourceError


SEC_PAYLOAD = {
    "cik": 1181412,
    "name": "Space Exploration Technologies Corp.",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0001181412-26-000003",
                "0001181412-26-000002",
                "0001181412-26-000001",
            ],
            "filingDate": ["2026-06-12", "2026-06-11", "2026-05-20"],
            "form": ["424B4", "8-K", "S-1"],
            "primaryDocument": ["spacex-424b4.htm", "spacex-8k.htm", "spacex-s1.htm"],
            "primaryDocDescription": [
                "Prospectus filed pursuant to Rule 424(b)(4)",
                "Current report",
                "Registration statement",
            ],
        }
    },
}


def test_parses_official_sec_filing_metadata_only() -> None:
    requested: dict[str, object] = {}

    def fake_fetcher(url: str, timeout: float, user_agent: str) -> bytes:
        requested.update(url=url, timeout=timeout, user_agent=user_agent)
        return json.dumps(SEC_PAYLOAD).encode()

    articles = SECEdgarNewsAdapter(
        user_agent="Personal Investment Assistant test@example.com",
        fetcher=fake_fetcher,
    ).fetch_recent("SPCX", 2)

    assert len(articles) == 2
    assert articles[0].filing_form == "424B4"
    assert articles[0].filing_description == "Prospectus filed pursuant to Rule 424(b)(4)"
    assert articles[0].published_at.isoformat() == "2026-06-12T00:00:00+00:00"
    assert articles[0].url == (
        "https://www.sec.gov/Archives/edgar/data/1181412/"
        "000118141226000003/spacex-424b4.htm"
    )
    assert articles[0].summary is None
    assert (
        articles[0].content_classification is ContentClassification.REGULATORY_FILING
    )
    assert requested == {
        "url": "https://data.sec.gov/submissions/CIK0001181412.json",
        "timeout": 15.0,
        "user_agent": "Personal Investment Assistant test@example.com",
    }


def test_returns_no_filings_for_ticker_without_configured_cik() -> None:
    adapter = SECEdgarNewsAdapter(
        user_agent="test@example.com",
        fetcher=lambda url, timeout, user_agent: pytest.fail("must not fetch"),
    )

    assert adapter.fetch_recent("UNKNOWN", 5) == []


def test_sec_network_failure_is_clear() -> None:
    def failing_fetcher(url: str, timeout: float, user_agent: str) -> bytes:
        raise TimeoutError("timed out")

    adapter = SECEdgarNewsAdapter(user_agent="test@example.com", fetcher=failing_fetcher)
    with pytest.raises(NewsSourceError, match="request failed"):
        adapter.fetch_recent("SPCX", 5)


FORM4_XML = b"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Musk Elon</rptOwnerName></reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-16</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>12500</value></transactionShares>
        <transactionPricePerShare><value>42.50</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_fetches_structured_form4_fields_without_article_text() -> None:
    payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["0001181412-26-000004"],
                "filingDate": ["2026-06-17"],
                "form": ["4"],
                "primaryDocument": ["ownership.xml"],
                "primaryDocDescription": ["FORM 4"],
                "items": [""],
            }
        }
    }
    requested_urls: list[str] = []

    def fake_fetcher(url: str, timeout: float, user_agent: str) -> bytes:
        requested_urls.append(url)
        return FORM4_XML if url.endswith("ownership.xml") else json.dumps(payload).encode()

    article = SECEdgarNewsAdapter(
        user_agent="Personal Investment Assistant test@example.com",
        fetcher=fake_fetcher,
    ).fetch_recent("SPCX", 1)[0]

    assert len(requested_urls) == 2
    assert article.summary is None
    assert len(article.insider_transactions) == 1
    transaction = article.insider_transactions[0]
    assert transaction.reporting_person == "Musk Elon"
    assert transaction.transaction_date.isoformat() == "2026-06-16"
    assert transaction.transaction_code == "P"
    assert transaction.transaction_type == "open-market purchase"
    assert str(transaction.shares) == "12500"
    assert str(transaction.price_per_share) == "42.50"


def test_form4_detail_failure_does_not_infer_or_remove_filing_metadata() -> None:
    payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["0001181412-26-000004"],
                "filingDate": ["2026-06-17"],
                "form": ["4"],
                "primaryDocument": ["ownership.xml"],
                "primaryDocDescription": ["FORM 4"],
            }
        }
    }

    def fake_fetcher(url: str, timeout: float, user_agent: str) -> bytes:
        if url.endswith("ownership.xml"):
            raise TimeoutError("detail request timed out")
        return json.dumps(payload).encode()

    article = SECEdgarNewsAdapter(
        user_agent="Personal Investment Assistant test@example.com",
        fetcher=fake_fetcher,
    ).fetch_recent("SPCX", 1)[0]

    assert article.filing_form == "4"
    assert article.insider_transactions == ()


def test_form4_uses_raw_sec_xml_path_for_xsl_display_url() -> None:
    payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["0001181412-26-000004"],
                "filingDate": ["2026-06-17"],
                "form": ["4"],
                "primaryDocument": ["xslF345X06/ownership.xml"],
                "primaryDocDescription": ["FORM 4"],
            }
        }
    }
    requested_urls: list[str] = []

    def fake_fetcher(url: str, timeout: float, user_agent: str) -> bytes:
        requested_urls.append(url)
        if "submissions" in url:
            return json.dumps(payload).encode()
        if "/xslF345X06/" in url:
            raise OSError("display endpoint did not return raw XML")
        return FORM4_XML

    article = SECEdgarNewsAdapter(
        user_agent="Personal Investment Assistant test@example.com",
        fetcher=fake_fetcher,
    ).fetch_recent("SPCX", 1)[0]

    assert requested_urls[1].endswith("/000118141226000004/ownership.xml")
    assert article.insider_transactions[0].transaction_type == "open-market purchase"
