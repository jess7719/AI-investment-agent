"""Bounded official SEC filing extraction with fair-access pacing and caching."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from investment_assistant.adapters.content_cache import ContentCache
from investment_assistant.domain.evidence import EvidenceDepth, ExtractedEvidence
from investment_assistant.domain.news import NewsArticle

MAX_FILING_EXTRACT_CHARS = 5_000
MAX_SECTION_CHARS = 2_500
MAX_SUMMARY_CHARS = 1_000
ITEM_PATTERN = re.compile(r"^item\s+(\d+\.\d+)\s*(.*)$", re.IGNORECASE)
EXHIBIT_PATTERN = re.compile(r"(?:exhibit\s+\d+[\w.-]*|EX-\d+[\w.-]*)", re.IGNORECASE)

Fetcher = Callable[[str, str], bytes]


class SecRateLimiter:
    """Keep this process at five SEC requests/second or less."""

    _lock = threading.Lock()
    _last_request = 0.0

    def __init__(self, minimum_interval: float = 0.2) -> None:
        self._minimum_interval = minimum_interval

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self._minimum_interval:
                time.sleep(self._minimum_interval - elapsed)
            type(self)._last_request = time.monotonic()


class SECFilingContentAdapter:
    def __init__(
        self,
        user_agent: str,
        cache: ContentCache,
        fetcher: Fetcher | None = None,
        rate_limiter: SecRateLimiter | None = None,
    ) -> None:
        self._user_agent = user_agent.strip()
        self._cache = cache
        self._fetcher = fetcher or _fetch
        self._rate_limiter = rate_limiter or SecRateLimiter()

    def enrich(self, article: NewsArticle) -> ExtractedEvidence:
        if not article.filing_form or "sec.gov" not in article.url.lower():
            return ExtractedEvidence(
                article,
                EvidenceDepth.HEADLINE_ONLY,
                None,
                article.url,
                official_url=article.url,
                access_note="Not an official SEC filing URL.",
            )
        suffix = ".xml" if article.url.lower().endswith(".xml") else ".html"
        cached = self._cache.read_bytes("sec-filings", article.url, suffix)
        cache_hit = cached is not None
        if cached is None:
            if not self._user_agent:
                return self._unavailable(article, "SEC User-Agent is not configured.")
            try:
                self._rate_limiter.wait()
                cached = self._download_with_fallback(article)
            except (OSError, TimeoutError):
                return self._unavailable(article, "Official filing content was unavailable.")
            self._cache.write_bytes("sec-filings", article.url, suffix, cached)

        form = article.filing_form.upper()
        filing_payload = extract_submission_document(cached, form)
        if form == "8-K":
            sections, exhibits, summary = extract_8k_content(filing_payload)
            content = "\n\n".join(f"{heading}\n{text}" for heading, text in sections)
            return ExtractedEvidence(
                article=article,
                evidence_depth=(
                    EvidenceDepth.OFFICIAL_FILING_EXTRACT
                    if content
                    else EvidenceDepth.HEADLINE_ONLY
                ),
                content=content or None,
                original_url=article.url,
                official_url=article.url,
                filing_sections=sections,
                exhibit_descriptions=exhibits,
                material_event_summary=summary,
                cache_hit=cache_hit,
                access_note=None if content else "No bounded 8-K item extract was found.",
            )
        if form in {"3", "3/A", "4", "4/A"}:
            ownership = extract_ownership_fields(filing_payload)
            content = ownership_text(ownership)
            return ExtractedEvidence(
                article=article,
                evidence_depth=(
                    EvidenceDepth.OFFICIAL_FILING_EXTRACT
                    if ownership
                    else EvidenceDepth.HEADLINE_ONLY
                ),
                content=content or None,
                original_url=article.url,
                official_url=article.url,
                structured_ownership=ownership,
                cache_hit=cache_hit,
                access_note=None if ownership else "Structured ownership fields were unavailable.",
            )
        return self._unavailable(article, "This filing type has no Phase 1 content extractor.")

    def _download_with_fallback(self, article: NewsArticle) -> bytes:
        urls = tuple(
            dict.fromkeys(
                (
                    _raw_sec_url(article.url),
                    article.url,
                    _submission_text_url(article.url),
                )
            )
        )
        last_error: Exception | None = None
        for url in urls:
            try:
                self._rate_limiter.wait()
                return self._fetcher(url, self._user_agent)
            except (OSError, TimeoutError) as error:
                last_error = error
        raise OSError("All official SEC document endpoints were unavailable.") from last_error

    @staticmethod
    def _unavailable(article: NewsArticle, note: str) -> ExtractedEvidence:
        return ExtractedEvidence(
            article,
            EvidenceDepth.HEADLINE_ONLY,
            None,
            article.url,
            official_url=article.url,
            access_note=note,
        )


def extract_8k_content(payload: bytes) -> tuple[
    tuple[tuple[str, str], ...], tuple[str, ...], str | None
]:
    soup = BeautifulSoup(payload, "lxml")
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()
    lines = [" ".join(unescape(line).split()) for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]
    sections: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    total = 0
    for line in lines:
        match = ITEM_PATTERN.match(line)
        if match:
            if current_heading and current_lines:
                text = _cap(" ".join(current_lines), MAX_SECTION_CHARS)
                sections.append((current_heading, text))
                total += len(text)
            current_heading = f"Item {match.group(1)} {match.group(2)}".strip()
            current_lines = []
            if total >= MAX_FILING_EXTRACT_CHARS:
                break
        elif current_heading and total < MAX_FILING_EXTRACT_CHARS:
            current_lines.append(line)
    if current_heading and current_lines and total < MAX_FILING_EXTRACT_CHARS:
        sections.append((current_heading, _cap(" ".join(current_lines), MAX_SECTION_CHARS)))
    sections = sections[:6]
    exhibits = tuple(
        dict.fromkeys(
            _cap(line, 240)
            for line in lines
            if EXHIBIT_PATTERN.search(line) and len(line) <= 500
        )
    )[:10]
    combined = " ".join(text for _, text in sections)
    summary = _extractive_summary(combined) if combined else None
    return tuple(sections), exhibits, summary


def extract_ownership_fields(payload: bytes) -> tuple[dict[str, str | None], ...]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return ()
    people = [text for owner in _elements(root, "reportingOwner") if (text := _text(owner, "rptOwnerName"))]
    person = ", ".join(people) or None
    relationship = _ownership_relationship(root)
    rows: list[dict[str, str | None]] = []
    for element_name in (
        "nonDerivativeTransaction",
        "derivativeTransaction",
        "nonDerivativeHolding",
        "derivativeHolding",
    ):
        for transaction in _elements(root, element_name):
            code = _text(transaction, "transactionCode")
            rows.append(
                {
                    "reporting_person": person,
                    "relationship_to_issuer": relationship,
                    "transaction_date": _text(transaction, "transactionDate"),
                    "transaction_code": code,
                    "transaction_type": _transaction_type(code),
                    "shares": _number_text(transaction, "transactionShares"),
                    "price_per_share": _number_text(transaction, "transactionPricePerShare"),
                    "ownership_after": _number_text(
                        transaction, "sharesOwnedFollowingTransaction"
                    ),
                    "security_title": _text(transaction, "securityTitle"),
                }
            )
    if not rows and (person or relationship):
        no_securities = (_text(root, "noSecuritiesOwned") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        rows.append(
            {
                "reporting_person": person,
                "relationship_to_issuer": relationship,
                "transaction_date": None,
                "transaction_code": None,
                "transaction_type": "other",
                "shares": None,
                "price_per_share": None,
                "ownership_after": None,
                "security_title": (
                    "no securities reported on initial ownership disclosure"
                    if no_securities
                    else "initial ownership disclosure"
                ),
            }
        )
    return tuple(rows[:20])


def ownership_text(rows: tuple[dict[str, str | None], ...]) -> str:
    output = []
    for row in rows:
        output.append(
            "; ".join(
                f"{key.replace('_', ' ')}={value}"
                for key, value in row.items()
                if value not in (None, "")
            )
        )
    return _cap("\n".join(output), MAX_FILING_EXTRACT_CHARS)


def _fetch(url: str, user_agent: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "text/html, application/xml"},
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - validated SEC URL
        return response.read(MAX_FILING_EXTRACT_CHARS * 100)


def _raw_sec_url(url: str) -> str:
    return re.sub(r"/xslF345X\d+/", "/", url, flags=re.IGNORECASE)


def _submission_text_url(url: str) -> str:
    parts = urlparse(url)
    segments = parts.path.split("/")
    try:
        data_index = segments.index("data")
        cik = segments[data_index + 1]
        accession = segments[data_index + 2]
    except (ValueError, IndexError):
        return url
    if len(accession) != 18 or not accession.isdigit():
        return url
    dashed = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{dashed}.txt"


def extract_submission_document(payload: bytes, form: str) -> bytes:
    """Select the relevant bounded document from an EDGAR complete-submission text file."""

    if b"<DOCUMENT>" not in payload.upper():
        return payload
    decoded = payload.decode("utf-8", errors="replace")
    documents = re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", decoded, flags=re.DOTALL | re.IGNORECASE)
    normalized_form = form.removesuffix("/A")
    for document in documents:
        type_match = re.search(r"<TYPE>\s*([^\r\n<]+)", document, flags=re.IGNORECASE)
        if not type_match or type_match.group(1).strip().upper().removesuffix("/A") != normalized_form:
            continue
        text_match = re.search(r"<TEXT>(.*?)</TEXT>", document, flags=re.DOTALL | re.IGNORECASE)
        if text_match:
            content = text_match.group(1).strip()
            xml_wrapper = re.fullmatch(
                r"<XML>\s*(.*?)\s*</XML>", content, flags=re.DOTALL | re.IGNORECASE
            )
            if xml_wrapper:
                content = xml_wrapper.group(1).strip()
            return content.encode("utf-8")
    return payload


def _elements(root: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == name]


def _text(element: ElementTree.Element, name: str) -> str | None:
    for candidate in element.iter():
        if candidate.tag.rsplit("}", 1)[-1] != name:
            continue
        for descendant in candidate.iter():
            if descendant.tag.rsplit("}", 1)[-1] == "value" and descendant.text:
                return " ".join(descendant.text.split())
        if candidate.text and candidate.text.strip():
            return " ".join(candidate.text.split())
    return None


def _number_text(element: ElementTree.Element, name: str) -> str | None:
    value = _text(element, name)
    if value is None:
        return None
    try:
        return str(Decimal(value.replace(",", "").removeprefix("$")))
    except InvalidOperation:
        return value


def _ownership_relationship(root: ElementTree.Element) -> str | None:
    relationship = next(iter(_elements(root, "reportingOwnerRelationship")), None)
    if relationship is None:
        return None
    labels = []
    for field, label in (
        ("isDirector", "director"),
        ("isOfficer", "officer"),
        ("isTenPercentOwner", "10% owner"),
        ("isOther", "other"),
    ):
        if (_text(relationship, field) or "").lower() in {"1", "true", "yes"}:
            labels.append(label)
    for field in ("officerTitle", "otherText"):
        if value := _text(relationship, field):
            labels.append(value)
    return ", ".join(dict.fromkeys(labels)) or None


def _transaction_type(code: str | None) -> str:
    return {
        "P": "purchase",
        "S": "sale",
        "A": "grant",
        "M": "exercise",
        "C": "conversion",
    }.get(code or "", "other")


def _extractive_summary(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return _cap(" ".join(sentences[:3]), MAX_SUMMARY_CHARS)


def _cap(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"
