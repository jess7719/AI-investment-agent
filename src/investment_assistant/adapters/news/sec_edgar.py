"""Official SEC EDGAR submissions-metadata adapter."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from json import JSONDecodeError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from investment_assistant.domain.news import (
    ContentClassification,
    InsiderTransaction,
    NewsArticle,
    company_profile_for,
)
from investment_assistant.ports.news import NewsSourceError

JsonFetcher = Callable[[str, float, str], bytes]

FORM_DESCRIPTIONS = {
    "S-1": "Registration statement",
    "S-1/A": "Amended registration statement",
    "10-K": "Annual report",
    "10-Q": "Quarterly report",
    "8-K": "Current report",
    "424B4": "Prospectus filed pursuant to Rule 424(b)(4)",
}


class SECEdgarNewsAdapter:
    """Read filing index metadata without downloading filing documents."""

    name = "SEC EDGAR submissions"
    enabled = True
    unavailable_reason = None
    endpoint_template = "https://data.sec.gov/submissions/CIK{cik}.json"

    def __init__(
        self,
        user_agent: str,
        fetcher: JsonFetcher | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._user_agent = user_agent.strip()
        self._fetcher = fetcher or self._fetch_json
        self._timeout = timeout

    def fetch_recent(self, ticker: str, limit: int) -> list[NewsArticle]:
        profile = company_profile_for(ticker)
        if profile is None or profile.padded_cik is None:
            return []
        if not self._user_agent:
            raise NewsSourceError(
                "SEC EDGAR submissions is unavailable: SEC_USER_AGENT is not configured."
            )

        request_url = self.endpoint_template.format(cik=profile.padded_cik)
        try:
            payload = json.loads(self._fetcher(request_url, self._timeout, self._user_agent))
        except (OSError, TimeoutError, JSONDecodeError, UnicodeDecodeError) as error:
            raise NewsSourceError(
                f"{self.name} request failed ({type(error).__name__}). "
                "Check the network connection or try again later."
            ) from error

        recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload, dict) else {}
        if not isinstance(recent, dict):
            raise NewsSourceError(f"{self.name} returned an unexpected response.")

        accession_numbers = recent.get("accessionNumber", [])
        if not isinstance(accession_numbers, list):
            raise NewsSourceError(f"{self.name} returned an unexpected response.")

        articles: list[NewsArticle] = []
        for index, _accession in enumerate(accession_numbers[:limit]):
            parsed = self._parse_filing(profile.ticker, profile.sec_cik or "", recent, index)
            if parsed is not None:
                if parsed.filing_form and parsed.filing_form.upper() in {"4", "4/A"}:
                    parsed = self._add_form4_details(parsed)
                articles.append(parsed)
        return articles

    def _add_form4_details(self, article: NewsArticle) -> NewsArticle:
        """Read only structured ownership fields; a failure leaves metadata intact."""

        transactions: tuple[InsiderTransaction, ...] | None = None
        detail_urls = tuple(dict.fromkeys((_raw_ownership_url(article.url), article.url)))
        for detail_url in detail_urls:
            try:
                payload = self._fetcher(detail_url, self._timeout, self._user_agent)
                transactions = parse_form4_transactions(payload)
                break
            except (OSError, TimeoutError, UnicodeDecodeError, ElementTree.ParseError):
                continue
        if transactions is None:
            return article
        return NewsArticle(
            ticker=article.ticker,
            headline=article.headline,
            publisher=article.publisher,
            published_at=article.published_at,
            url=article.url,
            summary=article.summary,
            content_classification=article.content_classification,
            filing_form=article.filing_form,
            filing_description=article.filing_description,
            filing_items=article.filing_items,
            insider_transactions=transactions,
        )

    @staticmethod
    def _fetch_json(url: str, timeout: float, user_agent: str) -> bytes:
        accept = (
            "application/xml, text/xml;q=0.9, */*;q=0.8"
            if url.lower().endswith(".xml")
            else "application/json"
        )
        request = Request(
            url,
            headers={"Accept": accept, "User-Agent": user_agent},
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed SEC HTTPS
            return response.read()

    @staticmethod
    def _parse_filing(
        ticker: str,
        cik: str,
        recent: dict[str, object],
        index: int,
    ) -> NewsArticle | None:
        accession = _column_value(recent, "accessionNumber", index)
        filing_date = _column_value(recent, "filingDate", index)
        form = _column_value(recent, "form", index)
        primary_document = _column_value(recent, "primaryDocument", index)
        description = _column_value(recent, "primaryDocDescription", index)
        items = tuple(
            item.strip() for item in _column_value(recent, "items", index).split(",") if item.strip()
        )
        if not accession or not filing_date or not form:
            return None
        try:
            published_at = datetime.combine(
                datetime.strptime(filing_date, "%Y-%m-%d").date(), time(), timezone.utc
            )
        except ValueError:
            return None

        accession_compact = accession.replace("-", "")
        filing_file = primary_document or f"{accession}-index.html"
        official_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession_compact}/{filing_file}"
        )
        filing_description = description or FORM_DESCRIPTIONS.get(form, f"SEC Form {form}")
        return NewsArticle(
            ticker=ticker,
            headline=f"SEC {form}: {filing_description}",
            publisher="SEC EDGAR",
            published_at=published_at,
            url=official_url,
            summary=None,
            content_classification=ContentClassification.REGULATORY_FILING,
            filing_form=form,
            filing_description=filing_description,
            filing_items=items,
        )


def _column_value(recent: dict[str, object], column: str, index: int) -> str:
    values = recent.get(column, [])
    if not isinstance(values, list) or index >= len(values):
        return ""
    return str(values[index] or "").strip()


TRANSACTION_TYPES = {
    "P": "open-market purchase",
    "S": "open-market sale",
    "A": "grant or award",
    "M": "exercise or conversion",
    "C": "conversion",
}


def parse_form4_transactions(payload: bytes) -> tuple[InsiderTransaction, ...]:
    """Extract selected non-derivative and derivative transaction metadata."""

    root = ElementTree.fromstring(payload)
    reporting_people = tuple(
        text
        for owner in _elements(root, "reportingOwner")
        if (text := _descendant_text(owner, "rptOwnerName"))
    )
    reporting_person = ", ".join(reporting_people) or None
    relationship = _relationship_text(root)
    transactions: list[InsiderTransaction] = []
    for element_name in ("nonDerivativeTransaction", "derivativeTransaction"):
        for transaction in _elements(root, element_name):
            code = _descendant_text(transaction, "transactionCode")
            date_text = _descendant_text(transaction, "transactionDate")
            transaction_date = None
            if date_text:
                try:
                    transaction_date = datetime.strptime(date_text, "%Y-%m-%d").date()
                except ValueError:
                    pass
            transactions.append(
                InsiderTransaction(
                    reporting_person=reporting_person,
                    transaction_date=transaction_date,
                    transaction_code=code,
                    transaction_type=TRANSACTION_TYPES.get(
                        code or "", f"other transaction{f' ({code})' if code else ''}"
                    ),
                    shares=_decimal_value(transaction, "transactionShares"),
                    price_per_share=_decimal_value(
                        transaction, "transactionPricePerShare"
                    ),
                    relationship_to_issuer=relationship,
                    ownership_after=_decimal_value(
                        transaction, "sharesOwnedFollowingTransaction"
                    ),
                )
            )
    return tuple(transactions)


def _elements(root: ElementTree.Element, local_name: str) -> list[ElementTree.Element]:
    return [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == local_name]


def _descendant_text(element: ElementTree.Element, local_name: str) -> str | None:
    for descendant in element.iter():
        if descendant.tag.rsplit("}", 1)[-1] != local_name:
            continue
        value = next(
            (
                child.text.strip()
                for child in descendant.iter()
                if child.tag.rsplit("}", 1)[-1] == "value" and child.text
            ),
            None,
        )
        if value:
            return value
        if descendant.text and descendant.text.strip():
            return descendant.text.strip()
    return None


def _decimal_value(element: ElementTree.Element, local_name: str) -> Decimal | None:
    value = _descendant_text(element, local_name)
    if value is None:
        return None
    try:
        return Decimal(value.replace(",", "").removeprefix("$"))
    except InvalidOperation:
        return None


def _raw_ownership_url(display_url: str) -> str:
    """Convert SEC's virtual XSL display path to its raw archive document path."""

    return re.sub(r"/xslF345X\d+/", "/", display_url, flags=re.IGNORECASE)


def _relationship_text(root: ElementTree.Element) -> str | None:
    relationship = next(iter(_elements(root, "reportingOwnerRelationship")), None)
    if relationship is None:
        return None
    labels: list[str] = []
    boolean_fields = (
        ("isDirector", "director"),
        ("isOfficer", "officer"),
        ("isTenPercentOwner", "10% owner"),
        ("isOther", "other"),
    )
    for field, label in boolean_fields:
        value = (_descendant_text(relationship, field) or "").lower()
        if value in {"1", "true", "yes"}:
            labels.append(label)
    officer_title = _descendant_text(relationship, "officerTitle")
    other_text = _descendant_text(relationship, "otherText")
    if officer_title:
        labels.append(officer_title)
    if other_text:
        labels.append(other_text)
    return ", ".join(dict.fromkeys(labels)) or None
