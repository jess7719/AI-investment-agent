import json
import hashlib
from pathlib import Path
from typing import Any

from investment_assistant import cli
from tests.fixtures.long_sentence_deep_response import (
    long_sentence_response_envelope,
)


def test_validation_only_command_repairs_fixture_and_never_calls_openai(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    source = tmp_path / "long-sentence-response.json"
    source.write_text(
        json.dumps(long_sentence_response_envelope()),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("validate-report must never construct an OpenAI client")

    monkeypatch.setattr(cli, "OpenAIDeepResearchReasoner", forbidden)

    assert cli.run_validate_report(source) == 0

    output = capsys.readouterr().out
    markdown = source.with_suffix(".md")
    assert "API request completed: no" in output
    assert "Report validation: repaired" in output
    assert "sentence was split locally" in output
    assert markdown.exists()
    rendered = markdown.read_text(encoding="utf-8")
    assert "input=2100, cached_input=400, output=1600, total=3700" in rendered
    assert "141.73" in rendered
    assert "141.72999572753906" not in rendered
    assert "[Market Data]" in rendered
    assert "https://example.com/research" in rendered


def test_offline_recovery_preserves_rejected_sources_and_never_calls_openai(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    rejected = tmp_path / "reports" / "2026-07-16" / "rejected"
    rejected.mkdir(parents=True)
    source = rejected / "SPCX-deep-20260716T120000000000Z.json"
    summary = rejected / "SPCX-deep-20260716T120000000000Z-validation.json"
    envelope = long_sentence_response_envelope()
    envelope.update(
        {
            "artifact_status": "rejected",
            "approved_for_investment_use": False,
            "validation": {
                "status": "rejected",
                "issues": [
                    {
                        "code": "fatal_quality_validation",
                        "severity": "fatal",
                        "message": "Legacy exact numeric comparison failed.",
                        "location": None,
                    }
                ],
            },
        }
    )
    source.write_text(json.dumps(envelope), encoding="utf-8")
    summary.write_text(json.dumps({"validation": envelope["validation"]}), encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    summary_hash = hashlib.sha256(summary.read_bytes()).hexdigest()
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline recovery must never construct an OpenAI client")

    monkeypatch.setattr(cli, "OpenAIDeepResearchReasoner", forbidden)

    assert cli.run_recover_report(source) == 0

    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256(summary.read_bytes()).hexdigest() == summary_hash
    recovered_json = tmp_path / "reports" / "2026-07-16" / "SPCX-deep-recovered.json"
    recovered_md = tmp_path / "reports" / "2026-07-16" / "SPCX-deep-recovered.md"
    assert recovered_json.exists()
    assert recovered_md.exists()
    recovered = json.loads(recovered_json.read_text(encoding="utf-8"))
    assert recovered["artifact_status"] == "recovered_offline"
    assert recovered["recovery"]["additional_api_call_made"] is False
    assert recovered["recovery"]["original_response_id"] == ("resp_fixture_long_sentence")
    output = capsys.readouterr().out
    assert "API request completed: no" in output
    assert "Additional paid request: no" in output
    assert "Report validation: repaired" in output


def test_offline_recovery_repairs_evidence_scope_and_preserves_citations(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    rejected = tmp_path / "reports" / "2026-10-18" / "rejected"
    rejected.mkdir(parents=True)
    source = rejected / "BRNT.L-deep-20261018T120000000000Z.json"
    summary = rejected / "BRNT.L-deep-20261018T120000000000Z-validation.json"
    envelope = long_sentence_response_envelope()
    envelope["ticker"] = "BRNT.L"
    envelope["report_date"] = "2026-10-18"
    envelope["report"]["key_evidence"][0]["fact"] = (
        "The complete filing shows proceeds of 25 billion."
    )
    envelope["validation_context"]["evidence"]["ranked_information_items"][0][
        "content_extract"
    ] = "The supplied filing extract states proceeds of 25 billion."
    envelope.update(
        {
            "artifact_status": "rejected",
            "approved_for_investment_use": False,
            "validation": {
                "status": "rejected",
                "issues": [
                    {
                        "code": "fatal_quality_validation",
                        "severity": "fatal",
                        "message": "Report implies unsupported full-text access.",
                        "location": None,
                    }
                ],
            },
        }
    )
    source.write_text(json.dumps(envelope), encoding="utf-8")
    summary.write_text(json.dumps({"validation": envelope["validation"]}), encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    summary_hash = hashlib.sha256(summary.read_bytes()).hexdigest()
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline recovery must never construct an OpenAI client")

    monkeypatch.setattr(cli, "OpenAIDeepResearchReasoner", forbidden)

    assert cli.run_recover_report(source) == 0

    recovered_path = tmp_path / "reports/2026-10-18/BRNT.L-deep-recovered.json"
    recovered = json.loads(recovered_path.read_text(encoding="utf-8"))
    repaired_item = recovered["repaired_report"]["key_evidence"][0]
    assert repaired_item["reference"] == "[Filing 1]"
    assert "supplied filing extract indicates" in repaired_item["fact"]
    assert recovered["validation"]["status"] == "repaired"
    assert recovered["recovery"]["additional_api_call_made"] is False
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256(summary.read_bytes()).hexdigest() == summary_hash
