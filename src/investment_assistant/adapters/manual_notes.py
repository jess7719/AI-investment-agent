"""Private local notes supplied manually by the investor."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from investment_assistant.domain.evidence import EvidenceDepth, ExtractedEvidence, ManualNote
from investment_assistant.domain.news import ContentClassification, NewsArticle

TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")


class ManualNoteError(ValueError):
    pass


class ManualNoteRepository:
    def __init__(self, root: Path) -> None:
        self._root = root

    def add(self, note: ManualNote) -> Path:
        ticker = _ticker(note.ticker)
        directory = self._root / ticker
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = note.date_added.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = directory / f"{timestamp}.json"
        value = asdict(note)
        value["publication_date"] = note.publication_date.isoformat()
        value["date_added"] = note.date_added.isoformat()
        value["key_facts"] = list(note.key_facts)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, ticker: str) -> list[ManualNote]:
        directory = self._root / _ticker(ticker)
        if not directory.exists():
            return []
        notes: list[ManualNote] = []
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                notes.append(note_from_mapping(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, ManualNoteError):
                continue
        return notes


def load_note_file(path: Path, ticker: str) -> ManualNote:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManualNoteError("Note file must be readable UTF-8 JSON.") from error
    if not isinstance(value, dict):
        raise ManualNoteError("Note file must contain one JSON object.")
    value["ticker"] = ticker
    value.setdefault("date_added", datetime.now(timezone.utc).isoformat())
    return note_from_mapping(value)


def note_from_mapping(value: dict[str, object]) -> ManualNote:
    try:
        ticker = _ticker(str(value["ticker"]))
        publisher = _required(value, "publisher", 150)
        headline = _required(value, "headline", 300)
        url = _url(_required(value, "url", 1_000))
        publication_date = date.fromisoformat(str(value["publication_date"]))
        summary = _required(value, "summary", 5_000)
        why = _required(value, "why_it_matters", 2_000)
        date_added = datetime.fromisoformat(str(value["date_added"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as error:
        raise ManualNoteError(f"Invalid or missing note field: {error}") from error
    facts_value = value.get("key_facts", [])
    if isinstance(facts_value, str):
        facts = tuple(line.strip() for line in facts_value.splitlines() if line.strip())
    elif isinstance(facts_value, list):
        facts = tuple(str(item).strip()[:500] for item in facts_value if str(item).strip())
    else:
        raise ManualNoteError("key_facts must be a JSON list or newline-separated string.")
    return ManualNote(
        ticker,
        publisher,
        headline,
        url,
        publication_date,
        summary,
        facts[:12],
        why,
        date_added,
    )


def note_to_evidence(note: ManualNote) -> ExtractedEvidence:
    article = NewsArticle(
        ticker=note.ticker,
        headline=note.headline,
        publisher=note.publisher,
        published_at=datetime.combine(
            note.publication_date, datetime.min.time(), timezone.utc
        ),
        url=note.url,
        summary=note.summary,
        content_classification=ContentClassification.FACTUAL_FINANCIAL_REPORTING,
        relevance_score=90,
        importance_score=50,
        source_confidence_score=70,
        recency_score=50,
        ranking_score=65,
    )
    content = "\n".join(
        (
            f"Investor summary: {note.summary}",
            *[f"Key fact: {fact}" for fact in note.key_facts],
            f"Why the investor thinks it matters: {note.why_it_matters}",
        )
    )
    return ExtractedEvidence(
        article,
        EvidenceDepth.USER_PROVIDED_NOTES,
        content[:5_000],
        note.url,
        access_note="Manually supplied notes; underlying subscription content was not fetched.",
    )


def _ticker(value: str) -> str:
    normalized = value.strip().upper()
    if not TICKER_PATTERN.fullmatch(normalized):
        raise ManualNoteError("Invalid ticker.")
    return normalized


def _required(value: dict[str, object], key: str, maximum: int) -> str:
    text = str(value[key]).strip()
    if not text:
        raise ManualNoteError(f"{key} cannot be empty.")
    return text[:maximum]


def _url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ManualNoteError("url must be an http or https URL.")
    return value
