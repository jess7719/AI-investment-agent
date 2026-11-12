"""Secret-free request diagnostics and ambiguous-billing protection."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class AmbiguousAttemptExistsError(RuntimeError):
    """Raised when the same evidence may already have incurred an API charge."""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        super().__init__(
            "A pending ambiguous attempt already exists for this evidence package."
        )
        self.paths = paths


@dataclass(frozen=True, slots=True)
class RequestAttempt:
    local_attempt_id: str
    path: Path
    ticker: str
    report_date: str
    evidence_package_path: Path
    evidence_package_hash: str
    model: str
    timeout_seconds: float
    max_output_tokens: int
    start_time: datetime


class AnalysisAttemptStore:
    def __init__(self, reports_root: Path) -> None:
        self._reports_root = reports_root

    def pending_for_hash(self, report_date: str, evidence_hash: str) -> tuple[Path, ...]:
        pending_root = self._reports_root / report_date / "pending"
        matches: list[Path] = []
        for path in pending_root.glob("*-deep-*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(value, dict)
                and value.get("artifact_status") == "pending_ambiguous_attempt"
                and value.get("evidence_package_hash") == evidence_hash
            ):
                matches.append(path.resolve())
        return tuple(matches)

    def begin(
        self,
        *,
        ticker: str,
        report_date: str,
        evidence_package_path: Path,
        evidence_package_hash: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        acknowledge_possible_duplicate_charge: bool,
        now: datetime | None = None,
    ) -> RequestAttempt:
        existing = self.pending_for_hash(report_date, evidence_package_hash)
        if existing and not acknowledge_possible_duplicate_charge:
            raise AmbiguousAttemptExistsError(existing)
        started = now or datetime.now(timezone.utc)
        local_attempt_id = f"attempt_{uuid4().hex}"
        slug = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = (
            self._reports_root
            / report_date
            / ".attempts"
            / f"{ticker.upper()}-deep-{slug}.json"
        )
        attempt = RequestAttempt(
            local_attempt_id=local_attempt_id,
            path=path,
            ticker=ticker.upper(),
            report_date=report_date,
            evidence_package_path=evidence_package_path.resolve(),
            evidence_package_hash=evidence_package_hash,
            model=model,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            start_time=started,
        )
        _write_json(path, _attempt_value(attempt, status="request_starting"))
        return attempt

    def mark_ambiguous(
        self,
        attempt: RequestAttempt,
        *,
        exception_category: str,
        openai_request_id: str | None,
        failure_time: datetime | None = None,
    ) -> Path:
        failed = failure_time or datetime.now(timezone.utc)
        slug = attempt.start_time.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = (
            self._reports_root
            / attempt.report_date
            / "pending"
            / f"{attempt.ticker}-deep-{slug}.json"
        )
        value = _attempt_value(attempt, status="pending_ambiguous_attempt")
        value.update(
            {
                "artifact_status": "pending_ambiguous_attempt",
                "request_failure_time": failed.isoformat(),
                "exception_category": exception_category,
                "openai_request_id": openai_request_id,
                "billing_status": "unknown",
                "automatic_retry_performed": False,
                "notice": (
                    "Billing status is unknown. Do not retry automatically. Check the "
                    "OpenAI Usage Dashboard before submitting another request."
                ),
            }
        )
        _write_json(path, value)
        attempt.path.unlink(missing_ok=True)
        return path.resolve()

    def finish(self, attempt: RequestAttempt) -> None:
        attempt.path.unlink(missing_ok=True)


def _attempt_value(attempt: RequestAttempt, *, status: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_status": status,
        "local_attempt_id": attempt.local_attempt_id,
        "ticker": attempt.ticker,
        "report_date": attempt.report_date,
        "evidence_package_path": str(attempt.evidence_package_path),
        "evidence_package_hash": attempt.evidence_package_hash,
        "request_start_time": attempt.start_time.isoformat(),
        "model": attempt.model,
        "timeout_seconds": attempt.timeout_seconds,
        "max_output_tokens": attempt.max_output_tokens,
    }


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
