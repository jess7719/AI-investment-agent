"""Atomic, secret-free persistence for received and rejected deep responses."""

from __future__ import annotations

import json
import os
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from investment_assistant.domain.deep_report import (
    DeepResearchReport,
    GeneratedDeepResearchReport,
)
from investment_assistant.domain.report_validation import ValidationSummary
from investment_assistant.adapters.openai_deep_report import MalformedDeepAPIResponse


@dataclass(frozen=True, slots=True)
class PendingDeepArtifact:
    path: Path
    envelope: dict[str, object]
    timestamp_slug: str


class DeepReportArtifactStore:
    def __init__(self, reports_root: Path) -> None:
        self._reports_root = reports_root

    def preserve_received(
        self,
        *,
        ticker: str,
        report_date: str,
        generated: GeneratedDeepResearchReport,
        evidence: dict[str, object],
        allowed_references: frozenset[str],
        allowed_main_clusters: frozenset[str],
        evidence_depth_by_reference: dict[str, object],
        configured_profile_fields: tuple[str, ...],
    ) -> PendingDeepArtifact:
        timestamp = generated.request_timestamp or datetime.now(timezone.utc)
        slug = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        envelope: dict[str, object] = {
            "schema_version": 2,
            "artifact_status": "received",
            "approved_for_investment_use": False,
            "notice": (
                "Received structured model response. Not approved for investment use "
                "until validation completes."
            ),
            "ticker": ticker,
            "report_date": report_date,
            "request_timestamp": timestamp.isoformat(),
            "response_id": generated.response_id,
            "response_status": generated.response_status,
            "incomplete_details": generated.incomplete_details,
            "model": generated.model,
            "usage": {
                "input_tokens": generated.usage.input_tokens,
                "cached_input_tokens": generated.usage.cached_input_tokens,
                "output_tokens": generated.usage.output_tokens,
                "total_tokens": generated.usage.total_tokens,
            },
            "raw_output": generated.raw_output,
            "report": generated.report.model_dump(mode="json"),
            "validation_context": {
                "allowed_references": sorted(allowed_references),
                "allowed_main_clusters": sorted(allowed_main_clusters),
                "evidence_depth_by_reference": {
                    reference: getattr(depth, "value", str(depth))
                    for reference, depth in evidence_depth_by_reference.items()
                },
                "configured_profile_fields": list(configured_profile_fields),
                "evidence": evidence,
            },
            "validation": {"status": "received", "issues": []},
        }
        path = self._reports_root / report_date / ".pending" / f"{ticker}-deep-{slug}.json"
        _write_secure_json(path, envelope)
        return PendingDeepArtifact(path, envelope, slug)

    def approve(
        self,
        pending: PendingDeepArtifact,
        *,
        ticker: str,
        report_date: str,
        repaired_report: DeepResearchReport,
        validation: ValidationSummary,
    ) -> Path:
        envelope = dict(pending.envelope)
        envelope.update(
            {
                "artifact_status": "approved",
                "approved_for_investment_use": True,
                "notice": "Validated research artifact; still not an automatic trade instruction.",
                "repaired_report": repaired_report.model_dump(mode="json"),
                "validation": validation.model_dump(mode="json"),
            }
        )
        path = self._reports_root / report_date / f"{ticker}-deep.json"
        _write_secure_json(path, envelope)
        pending.path.unlink(missing_ok=True)
        return path

    def reject(
        self,
        pending: PendingDeepArtifact,
        *,
        ticker: str,
        report_date: str,
        validation: ValidationSummary,
    ) -> tuple[Path, Path]:
        envelope = dict(pending.envelope)
        envelope.update(
            {
                "artifact_status": "rejected",
                "approved_for_investment_use": False,
                "notice": (
                    "REJECTED. This response is retained for recovery and is not approved "
                    "for investment use."
                ),
                "validation": validation.model_dump(mode="json"),
            }
        )
        rejected_root = self._reports_root / report_date / "rejected"
        path = rejected_root / f"{ticker}-deep-{pending.timestamp_slug}.json"
        summary_path = rejected_root / f"{ticker}-deep-{pending.timestamp_slug}-validation.json"
        _write_secure_json(path, envelope)
        _write_secure_json(
            summary_path,
            {
                "artifact_status": "rejected",
                "approved_for_investment_use": False,
                "ticker": ticker,
                "report_date": report_date,
                "response_id": envelope.get("response_id"),
                "model": envelope.get("model"),
                "usage": envelope.get("usage"),
                "validation": validation.model_dump(mode="json"),
                "rejected_response_path": str(path),
            },
        )
        pending.path.unlink(missing_ok=True)
        return path, summary_path

    def reject_malformed(
        self,
        *,
        ticker: str,
        report_date: str,
        receipt: MalformedDeepAPIResponse,
        validation: ValidationSummary,
    ) -> tuple[Path, Path]:
        slug = receipt.request_timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        rejected_root = self._reports_root / report_date / "rejected"
        path = rejected_root / f"{ticker}-deep-{slug}.json"
        summary_path = rejected_root / f"{ticker}-deep-{slug}-validation.json"
        usage = {
            "input_tokens": receipt.usage.input_tokens,
            "cached_input_tokens": receipt.usage.cached_input_tokens,
            "output_tokens": receipt.usage.output_tokens,
            "total_tokens": receipt.usage.total_tokens,
        }
        _write_secure_json(
            path,
            {
                "schema_version": 2,
                "artifact_status": "rejected",
                "approved_for_investment_use": False,
                "notice": (
                    "REJECTED MALFORMED RESPONSE. Retained for audit only and not "
                    "approved for investment use."
                ),
                "ticker": ticker,
                "report_date": report_date,
                "request_timestamp": receipt.request_timestamp.isoformat(),
                "response_id": receipt.response_id,
                "response_status": receipt.response_status,
                "incomplete_details": receipt.incomplete_details,
                "model": receipt.model,
                "usage": usage,
                "raw_output": receipt.raw_output,
                "validation": validation.model_dump(mode="json"),
            },
        )
        _write_secure_json(
            summary_path,
            {
                "artifact_status": "rejected",
                "approved_for_investment_use": False,
                "ticker": ticker,
                "report_date": report_date,
                "response_id": receipt.response_id,
                "response_status": receipt.response_status,
                "incomplete_details": receipt.incomplete_details,
                "model": receipt.model,
                "usage": usage,
                "validation": validation.model_dump(mode="json"),
                "rejected_response_path": str(path),
            },
        )
        return path, summary_path

    def save_recovered(
        self,
        *,
        source_path: Path,
        source_envelope: dict[str, object],
        repaired_report: DeepResearchReport,
        validation: ValidationSummary,
        recovery_timestamp: datetime,
    ) -> Path:
        """Save a separately auditable recovery without changing the rejected source."""

        ticker = str(source_envelope.get("ticker") or "UNKNOWN").upper()
        report_date = str(source_envelope.get("report_date") or "unknown-date")
        validation_path = source_path.with_name(f"{source_path.stem}-validation.json")
        original_validation = source_envelope.get("validation")
        if validation_path.exists():
            try:
                summary = load_response_envelope(validation_path)
                original_validation = summary.get("validation", original_validation)
            except ValueError:
                pass
        recovered = dict(source_envelope)
        recovered.update(
            {
                "artifact_status": "recovered_offline",
                "approved_for_investment_use": True,
                "notice": (
                    "RECOVERED OFFLINE from a previously rejected paid response. "
                    "Validated research support; no additional API call was made."
                ),
                "repaired_report": repaired_report.model_dump(mode="json"),
                "validation": validation.model_dump(mode="json"),
                "recovery": {
                    "recovery_timestamp": recovery_timestamp.isoformat(),
                    "recovered_from": str(source_path.resolve()),
                    "original_response_id": source_envelope.get("response_id"),
                    "original_request_timestamp": source_envelope.get("request_timestamp"),
                    "original_model": source_envelope.get("model"),
                    "original_usage": source_envelope.get("usage"),
                    "original_rejection_reason": original_validation,
                    "original_validation_summary_path": (
                        str(validation_path.resolve()) if validation_path.exists() else None
                    ),
                    "original_artifact_sha256": _sha256(source_path),
                    "original_validation_summary_sha256": (
                        _sha256(validation_path) if validation_path.exists() else None
                    ),
                    "additional_api_call_made": False,
                },
            }
        )
        path = self._reports_root / report_date / f"{ticker}-deep-recovered.json"
        _write_secure_json(path, recovered)
        return path


def load_response_envelope(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Structured response file must be readable UTF-8 JSON.") from error
    if not isinstance(value, dict):
        raise ValueError("Structured response file must contain one JSON object.")
    return value


def _write_secure_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
