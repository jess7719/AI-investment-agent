"""Immutable, hash-verified evidence packages for separated deep analysis."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from investment_assistant.domain.evidence import EvidenceDepth
from investment_assistant.services.deep_report_service import PreparedDeepResearch
from investment_assistant.services.research_service import PreparedResearch


class EvidencePackageError(ValueError):
    """Raised when a saved evidence package is missing, corrupt, or altered."""


@dataclass(frozen=True, slots=True)
class SavedEvidencePackage:
    path: Path
    sha256: str
    evidence_timestamp: datetime
    prepared: PreparedDeepResearch
    reused: bool


class EvidencePackageStore:
    def __init__(self, reports_root: Path) -> None:
        self._reports_root = reports_root

    def canonical_path(self, ticker: str, report_date: date) -> Path:
        return (
            self._reports_root
            / report_date.isoformat()
            / "evidence"
            / f"{ticker.upper()}-deep-evidence.json"
        )

    def save(
        self,
        prepared: PreparedDeepResearch,
        *,
        refresh: bool = False,
        now: datetime | None = None,
    ) -> SavedEvidencePackage:
        timestamp = now or datetime.now(timezone.utc)
        canonical = self.canonical_path(
            prepared.research.ticker, prepared.research.report_date
        )
        if canonical.exists() and not refresh:
            loaded = self.load(canonical)
            return SavedEvidencePackage(
                loaded.path,
                loaded.sha256,
                loaded.evidence_timestamp,
                loaded.prepared,
                True,
            )
        path = canonical
        if refresh and canonical.exists():
            slug = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            path = canonical.with_name(f"{prepared.research.ticker}-deep-evidence-{slug}.json")

        body = _package_body(prepared, timestamp)
        digest = _hash_value(body)
        envelope = {**body, "evidence_package_sha256": digest}
        _write_immutable_json(path, envelope)
        return SavedEvidencePackage(path, digest, timestamp, prepared, False)

    def load(self, path: Path) -> SavedEvidencePackage:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise EvidencePackageError(
                "Evidence package must be readable UTF-8 JSON."
            ) from error
        if not isinstance(envelope, dict):
            raise EvidencePackageError("Evidence package must contain one JSON object.")
        expected = str(envelope.get("evidence_package_sha256") or "")
        body = dict(envelope)
        body.pop("evidence_package_sha256", None)
        actual = _hash_value(body)
        if not expected or actual != expected:
            raise EvidencePackageError(
                "Evidence package hash mismatch; the package may have been altered."
            )
        if envelope.get("artifact_status") != "immutable_evidence_package":
            raise EvidencePackageError("File is not an immutable deep evidence package.")
        prepared = _prepared_from_envelope(envelope)
        try:
            timestamp = datetime.fromisoformat(str(envelope["evidence_timestamp"]))
        except (KeyError, ValueError) as error:
            raise EvidencePackageError("Evidence timestamp is missing or invalid.") from error
        return SavedEvidencePackage(path.resolve(), actual, timestamp, prepared, True)


def _package_body(
    prepared: PreparedDeepResearch, timestamp: datetime
) -> dict[str, object]:
    research = prepared.research
    return {
        "schema_version": 1,
        "artifact_status": "immutable_evidence_package",
        "notice": (
            "Immutable evidence prepared locally. This file contains no API key and "
            "does not prove that an OpenAI request was made."
        ),
        "ticker": research.ticker,
        "report_date": research.report_date.isoformat(),
        "evidence_timestamp": timestamp.isoformat(),
        "source_retrievals": _source_retrievals(research, prepared, timestamp),
        "research": {
            "evidence": research.evidence,
            "allowed_references": sorted(research.allowed_references),
            "news_count": research.news_count,
            "filing_count": research.filing_count,
            "evidence_depth_counts": [list(item) for item in research.evidence_depth_counts],
            "extracted_content_characters": research.extracted_content_characters,
        },
        "deep_request": {
            "request_json": prepared.request_json,
            "allowed_main_clusters": sorted(prepared.allowed_main_clusters),
            "evidence_depth_by_reference": {
                reference: depth.value
                for reference, depth in prepared.evidence_depth_by_reference.items()
            },
            "profile_configured": prepared.profile_configured,
            "profile_field_count": prepared.profile_field_count,
            "profile_configured_fields": list(prepared.profile_configured_fields),
        },
    }


def _source_retrievals(
    research: PreparedResearch,
    prepared: PreparedDeepResearch,
    timestamp: datetime,
) -> list[dict[str, object]]:
    evidence = research.evidence
    retrieved = timestamp.isoformat()
    market = evidence.get("market_data")
    latest_date = market.get("latest_trading_date") if isinstance(market, dict) else None
    scope = evidence.get("scope")
    asset_type = scope.get("asset_type") if isinstance(scope, dict) else "equity"
    components = [
        _component("market_data", retrieved, market is not None, _is_stale_date(latest_date)),
        _component(
            "market_context",
            retrieved,
            evidence.get("market_context") is not None,
            False,
        ),
        (
            _component(
                "commodity_etp_context",
                retrieved,
                evidence.get("commodity_etp_context") is not None,
                False,
            )
            if asset_type == "commodity_etp"
            else _component(
                "fundamentals_and_valuation",
                retrieved,
                evidence.get("fundamental_and_valuation_context") is not None,
                False,
            )
        ),
        _component("news", retrieved, research.news_count > 0, False, research.news_count),
        _component(
            "regulatory_filings",
            retrieved,
            research.filing_count > 0,
            False,
            research.filing_count,
        ),
        _component(
            "investor_profile",
            retrieved,
            prepared.profile_configured,
            False,
            prepared.profile_field_count,
        ),
    ]
    return components


def _component(
    name: str,
    retrieved_at: str,
    available: bool,
    stale: bool,
    item_count: int | None = None,
) -> dict[str, object]:
    return {
        "component": name,
        "retrieved_at": retrieved_at,
        "status": "available" if available else "unavailable",
        "stale": stale,
        "item_count": item_count,
    }


def _is_stale_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        supplied = date.fromisoformat(value)
    except ValueError:
        return False
    return (date.today() - supplied).days > 5


def _prepared_from_envelope(envelope: dict[str, object]) -> PreparedDeepResearch:
    try:
        research_value = _mapping(envelope["research"], "research")
        deep_value = _mapping(envelope["deep_request"], "deep_request")
        evidence = _mapping(research_value["evidence"], "research.evidence")
        ticker = str(envelope["ticker"]).upper()
        report_date = date.fromisoformat(str(envelope["report_date"]))
        depths_value = _mapping(
            deep_value["evidence_depth_by_reference"],
            "deep_request.evidence_depth_by_reference",
        )
        depths = {
            str(reference): EvidenceDepth(str(depth))
            for reference, depth in depths_value.items()
        }
        research = PreparedResearch(
            ticker=ticker,
            report_date=report_date,
            evidence=evidence,
            evidence_json=json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
            allowed_references=frozenset(
                str(item) for item in _list(research_value["allowed_references"])
            ),
            news_count=int(research_value["news_count"]),
            filing_count=int(research_value["filing_count"]),
            evidence_depth_counts=tuple(
                (str(item[0]), int(item[1]))
                for item in _list(research_value.get("evidence_depth_counts", []))
                if isinstance(item, list) and len(item) == 2
            ),
            extracted_content_characters=int(
                research_value.get("extracted_content_characters", 0)
            ),
        )
        return PreparedDeepResearch(
            research=research,
            request_json=str(deep_value["request_json"]),
            allowed_main_clusters=frozenset(
                str(item) for item in _list(deep_value["allowed_main_clusters"])
            ),
            evidence_depth_by_reference=depths,
            profile_configured=bool(deep_value["profile_configured"]),
            profile_field_count=int(deep_value["profile_field_count"]),
            profile_configured_fields=tuple(
                str(item) for item in _list(deep_value["profile_configured_fields"])
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EvidencePackageError(
            "Evidence package structure is incomplete or invalid."
        ) from error


def _mapping(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidencePackageError(f"{location} must be an object.")
    return value


def _list(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise EvidencePackageError("Evidence package field must be a list.")
    return value


def _hash_value(value: dict[str, object]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_immutable_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    except FileExistsError as error:
        raise EvidencePackageError(
            "Evidence package already exists and will not be overwritten."
        ) from error
    os.chmod(path, 0o400)
