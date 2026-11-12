from datetime import datetime, timezone
from pathlib import Path

from investment_assistant.adapters.content_cache import ContentCache
from investment_assistant.adapters.sec_filing_content import SECFilingContentAdapter
from investment_assistant.domain.evidence import EvidenceDepth
from investment_assistant.domain.news import ContentClassification, NewsArticle


class NoWait:
    def wait(self) -> None:
        pass


def filing(form: str, url: str) -> NewsArticle:
    return NewsArticle(
        ticker="SPCX",
        headline=f"SEC {form}",
        publisher="SEC EDGAR",
        published_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
        url=url,
        summary=None,
        content_classification=ContentClassification.REGULATORY_FILING,
        filing_form=form,
    )


def test_extracts_8k_items_exhibits_and_uses_cache(tmp_path: Path) -> None:
    html = b"""
    <html><body>
      <h2>Item 8.01 Other Events</h2>
      <p>The company completed an offering and received net proceeds for general purposes.</p>
      <h2>Item 9.01 Financial Statements and Exhibits</h2>
      <p>Exhibit 99.1 Press release dated June 26, 2026.</p>
    </body></html>
    """
    calls = 0

    def fetcher(url: str, user_agent: str) -> bytes:
        nonlocal calls
        calls += 1
        assert "Personal" in user_agent
        return html

    adapter = SECFilingContentAdapter(
        "Personal Research test@example.com",
        ContentCache(tmp_path),
        fetcher,
        NoWait(),
    )
    article = filing("8-K", "https://www.sec.gov/Archives/example.htm")

    first = adapter.enrich(article)
    second = adapter.enrich(article)

    assert calls == 1
    assert first.evidence_depth is EvidenceDepth.OFFICIAL_FILING_EXTRACT
    assert first.filing_sections[0][0].startswith("Item 8.01")
    assert "Exhibit 99.1" in first.exhibit_descriptions[0]
    assert first.material_event_summary
    assert second.cache_hit is True


def test_extracts_structured_ownership_fields(tmp_path: Path) -> None:
    xml = b"""<ownershipDocument>
      <reportingOwner><reportingOwnerId><rptOwnerName>Example Person</rptOwnerName></reportingOwnerId>
      <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>Chief Test Officer</officerTitle>
      </reportingOwnerRelationship></reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionDate><value>2026-06-15</value></transactionDate>
        <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
        <transactionAmounts><transactionShares><value>12000</value></transactionShares>
        <transactionPricePerShare><value>105.25</value></transactionPricePerShare></transactionAmounts>
        <postTransactionAmounts><sharesOwnedFollowingTransaction><value>500000</value>
        </sharesOwnedFollowingTransaction></postTransactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    adapter = SECFilingContentAdapter(
        "Personal Research test@example.com",
        ContentCache(tmp_path),
        lambda url, user_agent: xml,
        NoWait(),
    )

    evidence = adapter.enrich(
        filing("4", "https://www.sec.gov/Archives/xslF345X06/form4.xml")
    )

    row = evidence.structured_ownership[0]
    assert row["reporting_person"] == "Example Person"
    assert row["relationship_to_issuer"] == "officer, Chief Test Officer"
    assert row["transaction_type"] == "sale"
    assert row["shares"] == "12000"
    assert row["price_per_share"] == "105.25"
    assert row["ownership_after"] == "500000"


def test_falls_back_to_complete_submission_text(tmp_path: Path) -> None:
    submission = b"""<SEC-DOCUMENT><DOCUMENT><TYPE>8-K
    <TEXT><html><body><h2>Item 8.01 Other Events</h2>
    <p>A material event was announced in the official filing.</p></body></html></TEXT>
    </DOCUMENT></SEC-DOCUMENT>"""
    requested: list[str] = []

    def fetcher(url: str, user_agent: str) -> bytes:
        requested.append(url)
        if not url.endswith(".txt"):
            raise OSError("primary endpoint denied")
        return submission

    adapter = SECFilingContentAdapter(
        "Personal Research test@example.com",
        ContentCache(tmp_path),
        fetcher,
        NoWait(),
    )
    evidence = adapter.enrich(
        filing(
            "8-K",
            "https://www.sec.gov/Archives/edgar/data/1181412/"
            "000162828026045763/spcx-closing8xkjune2026.htm",
        )
    )

    assert requested[-1].endswith("0001628280-26-045763.txt")
    assert evidence.evidence_depth is EvidenceDepth.OFFICIAL_FILING_EXTRACT
    assert "material event" in (evidence.content or "").lower()


def test_unwraps_ownership_xml_from_complete_submission(tmp_path: Path) -> None:
    submission = b"""<SEC-DOCUMENT><DOCUMENT><TYPE>4
    <TEXT><XML><?xml version="1.0"?><ownershipDocument>
    <reportingOwner><reportingOwnerId><rptOwnerName>Example Owner</rptOwnerName>
    </reportingOwnerId></reportingOwner>
    <nonDerivativeTable><nonDerivativeTransaction>
    <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
    <transactionAmounts><transactionShares><value>1000</value></transactionShares>
    </transactionAmounts></nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument></XML></TEXT></DOCUMENT></SEC-DOCUMENT>"""

    def fetcher(url: str, user_agent: str) -> bytes:
        if not url.endswith(".txt"):
            raise OSError("use complete submission")
        return submission

    adapter = SECFilingContentAdapter(
        "Personal Research test@example.com",
        ContentCache(tmp_path),
        fetcher,
        NoWait(),
    )
    evidence = adapter.enrich(
        filing(
            "4",
            "https://www.sec.gov/Archives/edgar/data/1181412/"
            "000162828026044069/xslF345X06/form4.xml",
        )
    )

    assert evidence.structured_ownership[0]["reporting_person"] == "Example Owner"
    assert evidence.structured_ownership[0]["transaction_type"] == "purchase"


def test_form3_preserves_reporting_person_when_no_securities_are_reported(
    tmp_path: Path,
) -> None:
    xml = b"""<ownershipDocument><reportingOwner><reportingOwnerId>
    <rptOwnerName>Initial Owner</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>
    </reportingOwner><nonDerivativeTable/><derivativeTable/><noSecuritiesOwned>1</noSecuritiesOwned>
    </ownershipDocument>"""
    adapter = SECFilingContentAdapter(
        "Personal Research test@example.com",
        ContentCache(tmp_path),
        lambda url, user_agent: xml,
        NoWait(),
    )

    evidence = adapter.enrich(
        filing("3", "https://www.sec.gov/Archives/example-form3.xml")
    )

    row = evidence.structured_ownership[0]
    assert row["reporting_person"] == "Initial Owner"
    assert row["relationship_to_issuer"] == "director"
    assert row["transaction_type"] == "other"
    assert "no securities" in (row["security_title"] or "")
