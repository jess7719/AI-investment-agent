from pathlib import Path
from types import SimpleNamespace
from typing import Any

from investment_assistant import cli
from investment_assistant.config import Settings


def settings(api_key: str | None = None) -> Settings:
    return Settings(api_key, "gpt-test", None, "test@example.com")


def test_settings_uses_configurable_long_openai_timeout(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "240")

    configured = Settings.from_environment()

    assert configured.openai_timeout_seconds == 240.0


def test_reuses_existing_daily_report_without_preparing_or_calling_openai(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    report_path = tmp_path / "2026-07-14" / "SPCX.md"
    report_path.parent.mkdir()
    report_path.write_text("# Existing report", encoding="utf-8")

    class FakeService:
        def __init__(self, **kwargs: object) -> None:
            pass

        def report_path(self, ticker: str) -> Path:
            return report_path

        def prepare(self, ticker: str) -> None:
            raise AssertionError("cached report must prevent data and API work")

    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings("configured"))
    monkeypatch.setattr(cli, "ResearchService", FakeService)

    assert cli.run_analysis("SPCX", dry_run=False, force=False) == 0
    assert "# Existing report" in capsys.readouterr().out


def test_dry_run_never_constructs_openai_client(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    prepared = SimpleNamespace(
        ticker="SPCX",
        news_count=5,
        filing_count=3,
        character_count=4800,
        approximate_tokens=1200,
        extracted_content_characters=2500,
        evidence_depth_counts=(("official filing extract", 3), ("headline only", 5)),
        evidence_selection=None,
        evidence={},
    )

    class FakeService:
        def __init__(self, **kwargs: object) -> None:
            pass

        def report_path(self, ticker: str) -> Path:
            return tmp_path / "SPCX.md"

        def prepare(self, ticker: str) -> object:
            return prepared

    def forbidden_openai(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not construct or call OpenAI")

    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings())
    monkeypatch.setattr(cli, "ResearchService", FakeService)
    monkeypatch.setattr(cli, "OpenAIResearchReasoner", forbidden_openai)

    assert cli.run_analysis("SPCX", dry_run=True, force=False) == 0
    output = capsys.readouterr().out
    assert "no OpenAI request was made" in output
    assert "~1,200 tokens" in output
    assert "API key value: not displayed" in output


def test_missing_api_key_stops_before_data_collection(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    class FakeService:
        def __init__(self, **kwargs: object) -> None:
            pass

        def report_path(self, ticker: str) -> Path:
            return tmp_path / "SPCX.md"

        def prepare(self, ticker: str) -> None:
            raise AssertionError("missing key must stop before network collection")

    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings())
    monkeypatch.setattr(cli, "ResearchService", FakeService)

    assert cli.run_analysis("SPCX", dry_run=False, force=False) == 1
    assert "OPENAI_API_KEY is missing or empty" in capsys.readouterr().err
