import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from investment_assistant import cli
from investment_assistant.config import Settings
from investment_assistant.domain.deep_report import (
    DeepResearchReport,
    GeneratedDeepResearchReport,
)
from investment_assistant.domain.research import TokenUsage
from investment_assistant.ports.research import ResearchTransportError
from investment_assistant.services.deep_report_service import DeepReportService
from investment_assistant.services.evidence_packages import EvidencePackageStore
from tests.test_deep_report import deep_report_data, prepared_research


class FakeReasoner:
    calls = 0
    attempts_root: Path | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def generate(self, evidence_json: str) -> GeneratedDeepResearchReport:
        type(self).calls += 1
        if type(self).attempts_root is not None:
            assert list(type(self).attempts_root.glob("*.json"))
        return GeneratedDeepResearchReport(
            report=DeepResearchReport.model_validate(deep_report_data()),
            model="gpt-test",
            usage=TokenUsage(1000, 500, 1500),
            response_id="resp_test",
            request_timestamp=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
            raw_output=DeepResearchReport.model_validate(deep_report_data()).model_dump_json(),
            response_status="completed",
        )


class TimeoutReasoner:
    calls = 0

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def generate(self, evidence_json: str) -> GeneratedDeepResearchReport:
        type(self).calls += 1
        raise ResearchTransportError(
            "read timeout",
            category="read_timeout_after_submission",
            ambiguous_billing=True,
            request_id="req_timeout_123",
        )


def settings(secret: str | None = "super-secret-key") -> Settings:
    return Settings(
        secret,
        "gpt-test",
        None,
        "test@example.com",
        openai_timeout_seconds=180.0,
    )


def saved_package(root: Path) -> Path:
    reports = root / "reports"
    prepared = DeepReportService(reports).prepare(prepared_research())
    return EvidencePackageStore(reports).save(
        prepared,
        now=datetime(2026, 7, 26, 11, tzinfo=timezone.utc),
    ).path


def test_prepare_analysis_saves_package_without_openai(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    research = prepared_research()
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings(None))
    monkeypatch.setattr(
        cli,
        "build_research_service",
        lambda configured: SimpleNamespace(prepare=lambda ticker: research),
    )
    monkeypatch.setattr(
        cli,
        "OpenAIDeepResearchReasoner",
        lambda *args, **kwargs: pytest.fail("OpenAI must not be constructed"),
    )

    result = cli.run_prepare_analysis("SPCX", deep=True, refresh=False)

    assert result == 0
    package = tmp_path / "reports/2026-07-16/evidence/SPCX-deep-evidence.json"
    assert package.exists()
    value = json.loads(package.read_text(encoding="utf-8"))
    assert value["artifact_status"] == "immutable_evidence_package"
    assert value["source_retrievals"]
    assert all(item["retrieved_at"] for item in value["source_retrievals"])
    assert "OpenAI request completed: no" in capsys.readouterr().out


def test_same_date_package_is_reused_and_refresh_does_not_overwrite(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    store = EvidencePackageStore(reports)
    prepared = DeepReportService(reports).prepare(prepared_research())
    first = store.save(prepared, now=datetime(2026, 7, 26, 10, tzinfo=timezone.utc))
    original = first.path.read_bytes()

    reused = store.save(prepared, now=datetime(2026, 7, 26, 11, tzinfo=timezone.utc))
    refreshed = store.save(
        prepared,
        refresh=True,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
    )

    assert reused.reused is True
    assert reused.path == first.path
    assert refreshed.path != first.path
    assert first.path.read_bytes() == original


def test_analysis_from_evidence_never_calls_external_data(
    tmp_path: Path, monkeypatch: Any
) -> None:
    package = saved_package(tmp_path)
    original_hash = hashlib.sha256(package.read_bytes()).hexdigest()
    FakeReasoner.calls = 0
    FakeReasoner.attempts_root = tmp_path / "reports/2026-07-16/.attempts"
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings())
    monkeypatch.setattr(cli, "OpenAIDeepResearchReasoner", FakeReasoner)
    monkeypatch.setattr(
        cli,
        "build_research_service",
        lambda configured: pytest.fail("external evidence collection is forbidden"),
    )

    result = cli.run_analyse_from_evidence(
        package,
        acknowledge_possible_duplicate_charge=False,
    )

    assert result == 0
    assert FakeReasoner.calls == 1
    assert hashlib.sha256(package.read_bytes()).hexdigest() == original_hash
    assert (tmp_path / "reports/2026-07-16/SPCX-deep.md").exists()
    FakeReasoner.attempts_root = None


def test_ambiguous_timeout_creates_pending_record_and_blocks_duplicate(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    package = saved_package(tmp_path)
    TimeoutReasoner.calls = 0
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings())
    monkeypatch.setattr(cli, "OpenAIDeepResearchReasoner", TimeoutReasoner)

    first = cli.run_analyse_from_evidence(
        package,
        acknowledge_possible_duplicate_charge=False,
    )
    first_output = capsys.readouterr()
    second = cli.run_analyse_from_evidence(
        package,
        acknowledge_possible_duplicate_charge=False,
    )
    second_output = capsys.readouterr()

    assert first == 1
    assert second == 1
    assert TimeoutReasoner.calls == 1
    pending = list((tmp_path / "reports/2026-07-16/pending").glob("*.json"))
    assert len(pending) == 1
    value = json.loads(pending[0].read_text(encoding="utf-8"))
    assert value["exception_category"] == "read_timeout_after_submission"
    assert value["openai_request_id"] == "req_timeout_123"
    assert value["billing_status"] == "unknown"
    assert value["automatic_retry_performed"] is False
    combined = first_output.out + first_output.err + second_output.out + second_output.err
    assert "super-secret-key" not in combined
    assert "Billing status: unknown" in combined
    assert "acknowledge-possible-duplicate-charge" in combined


def test_cli_parses_new_evidence_commands() -> None:
    prepared = cli.build_parser().parse_args(
        ["prepare-analysis", "SPCX", "--deep", "--refresh"]
    )
    analysed = cli.build_parser().parse_args(
        [
            "analyse-from-evidence",
            "evidence.json",
            "--acknowledge-possible-duplicate-charge",
        ]
    )

    assert prepared.deep is True
    assert prepared.refresh is True
    assert analysed.acknowledge_possible_duplicate_charge is True
