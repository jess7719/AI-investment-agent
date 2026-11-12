from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from investment_assistant import cli
from investment_assistant.config import Settings
from investment_assistant.domain.evidence import EvidenceDepth, ExtractedEvidence
from investment_assistant.domain.deep_report import (
    DeepResearchReport,
    GeneratedDeepResearchReport,
)
from investment_assistant.domain.news import ContentClassification, NewsArticle
from investment_assistant.domain.research import TokenUsage
from investment_assistant.services.research_service import PreparedResearch
from tests.test_deep_report import deep_report_data


def test_deep_dry_run_never_constructs_openai(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    filing = ExtractedEvidence(
        article=NewsArticle(
            ticker="SPCX",
            headline="SEC 8-K filing",
            publisher="SEC EDGAR",
            published_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            url="https://www.sec.gov/example",
            summary=None,
            content_classification=ContentClassification.REGULATORY_FILING,
        ),
        evidence_depth=EvidenceDepth.OFFICIAL_FILING_EXTRACT,
        content="Bounded filing extract.",
        original_url="https://www.sec.gov/example",
        cache_hit=True,
    )
    research = PreparedResearch(
        ticker="SPCX",
        report_date=date.today(),
        evidence={
            "ranked_information_items": [
                {
                    "reference": "[Filing 1]",
                    "source": "SEC EDGAR",
                    "evidence_depth": "official filing extract",
                    "selection_role": "main",
                }
            ],
            "event_selection": {
                "main_event_clusters": [
                    {"cluster_id": "event-1", "selected_reference": "[Filing 1]"}
                ]
            },
            "market_context": {"comparisons": [{"symbol": "SPY"}]},
            "fundamental_and_valuation_context": {
                "metrics": [{"name": "revenue", "value": None}],
                "overall_quality": "unavailable",
            },
        },
        evidence_json="{}",
        allowed_references=frozenset({"[Market Data]", "[Filing 1]"}),
        news_count=0,
        filing_count=1,
        evidence_selection=None,
        evidence_depth_counts=(("official filing extract", 1),),
        extracted_content_characters=900,
        prepared_items=(filing,),
    )
    fake_research_service = SimpleNamespace(prepare=lambda ticker: research)
    monkeypatch.setattr(
        cli.Settings,
        "from_environment",
        lambda: Settings(None, "gpt-test", None, "test@example.com"),
    )
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "build_research_service", lambda settings: fake_research_service)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("deep dry-run must not construct OpenAI")

    monkeypatch.setattr(cli, "OpenAIDeepResearchReasoner", forbidden)

    assert cli.run_analysis("SPCX", dry_run=True, force=False, deep=True) == 0
    output = capsys.readouterr().out
    assert "no OpenAI request was made" in output
    assert "Compact views: 1-2 weeks, 1-3 months, and long-term thesis" in output
    assert "Confidence measures: data, catalyst interpretation" in output
    assert "Key evidence limit: 5 items" in output
    assert "Investor profile: not configured" in output
    assert "Estimated full input tokens:" in output
    assert "instructions" in output
    assert "structured schema" in output
    assert "[Filing 1] SEC EDGAR" in output
    assert "evidence=official filing extract" in output
    assert "cache=hit" in output
    assert "API key value: not displayed" in output


def test_paid_response_logging_includes_usage_model_status_and_path(
    tmp_path: Path, capsys: Any
) -> None:
    generated = GeneratedDeepResearchReport(
        report=DeepResearchReport.model_validate(deep_report_data()),
        model="gpt-test",
        usage=TokenUsage(2100, 1600, 3700, 400),
        response_id="resp_123",
        request_timestamp=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )
    path = tmp_path / "SPCX-deep.md"

    cli._print_api_completion(
        generated,
        validation_status="repaired",
        artifact_path=path,
    )

    output = capsys.readouterr().out
    assert "API request completed: yes" in output
    assert "Input tokens: 2100" in output
    assert "Cached input tokens: 400" in output
    assert "Output tokens: 1600" in output
    assert "Total tokens: 3700" in output
    assert "Model: gpt-test" in output
    assert "Response ID: resp_123" in output
    assert "Report validation: repaired" in output
    assert f"Report path: {path}" in output
