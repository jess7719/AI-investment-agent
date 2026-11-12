from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from investment_assistant import cli
from investment_assistant.config import Settings
from investment_assistant.services.research_service import PreparedResearch


def test_news_report_dry_run_never_constructs_openai(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    research = PreparedResearch(
        ticker="SPCX",
        report_date=date.today(),
        evidence={
            "ranked_information_items": [
                {
                    "reference": "[News 1]",
                    "evidence_depth": "headline only",
                    "selection_role": "main",
                }
            ],
            "event_selection": {
                "main_event_clusters": [
                    {"cluster_id": "event-1", "selected_reference": "[News 1]"}
                ],
                "noise_references": [],
            },
        },
        evidence_json="{}",
        allowed_references=frozenset({"[Market Data]", "[News 1]"}),
        news_count=1,
        filing_count=0,
        evidence_selection=None,
        evidence_depth_counts=(("headline only", 1),),
        extracted_content_characters=0,
    )
    fake_research_service = SimpleNamespace(prepare=lambda ticker: research)
    monkeypatch.setattr(
        cli.Settings,
        "from_environment",
        lambda: Settings(None, "gpt-test", None, "test@example.com"),
    )
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        cli, "build_research_service", lambda settings: fake_research_service
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not construct OpenAI")

    monkeypatch.setattr(cli, "OpenAIDailyNewsReasoner", forbidden)

    assert cli.run_news_report("SPCX", dry_run=True, force=False) == 0
    output = capsys.readouterr().out
    assert "no OpenAI request was made" in output
    assert "Investor profile: not configured" in output
    assert "API key value: not displayed" in output
