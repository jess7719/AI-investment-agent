from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from investment_assistant.domain.evidence import EvidenceDepth
from investment_assistant.domain.news_report import (
    DailyNewsReport,
    GeneratedDailyNewsReport,
    validate_daily_news_report,
)
from investment_assistant.domain.research import TokenUsage
from investment_assistant.adapters.investor_profiles import InvestorProfileRepository
from investment_assistant.services.daily_news_report_service import DailyNewsReportService
from investment_assistant.services.research_service import PreparedResearch


def report_data(cluster_id: str = "event-1", reference: str = "[Filing 1]") -> dict[str, Any]:
    point = {"text": "SpaceX filed a transaction report.", "references": [reference]}
    return {
        "todays_news_in_plain_english": {
            "sentences": [point],
            "overall_news_flow": "mixed",
        },
        "important_events": [
            {
                "cluster_id": cluster_id,
                "title": "A disclosed ownership transaction",
                "what_happened": point,
                "key_facts": [point],
                "why_it_matters": [point],
                "likely_stock_effect": "neutral",
                "time_horizon": "next few weeks",
                "evidence_strength": "strong",
                "evidence_type": "official filing extract",
                "evidence_reason": "The evidence is an official bounded filing extract.",
                "beginner_takeaway": "Check the transaction type before drawing conclusions.",
            }
        ],
        "noise_to_ignore": [],
        "overall_effect": {
            "business_case_effect": "unchanged",
            "primary_effect": "short-term market sentiment",
            "price_reaction_assessment": "unclear",
            "most_plausible_market_interpretation": point,
            "explanation": [point],
            "final_stance": "neutral",
        },
        "investor_action": {
            "profile_basis": "No investor profile is configured.",
            "if_already_owns": ["Check whether the transaction changes the original thesis."],
            "if_does_not_own": ["Wait for the transaction details before entering."],
            "specific_action_trigger": "Reassess if a material open-market sale is confirmed.",
        },
        "what_to_watch_next": [
            {
                "event_or_data": "Transaction details",
                "timeframe": "when the filing is updated",
                "positive_result_means": "No material new share supply.",
                "negative_result_means": "A large sale could pressure sentiment.",
                "references": [reference],
            }
        ],
    }


def prepared_research() -> PreparedResearch:
    evidence = {
        "ranked_information_items": [
            {
                "reference": "[Filing 1]",
                "evidence_depth": "official filing extract",
                "selection_role": "main",
            }
        ],
        "event_selection": {
            "main_event_clusters": [
                {"cluster_id": "event-1", "selected_reference": "[Filing 1]"}
            ],
            "noise_references": [],
        },
    }
    return PreparedResearch(
        ticker="SPCX",
        report_date=date(2026, 7, 15),
        evidence=evidence,
        evidence_json="{}",
        allowed_references=frozenset({"[Market Data]", "[Filing 1]"}),
        news_count=0,
        filing_count=1,
    )


def test_rejects_duplicate_event_clusters() -> None:
    data = report_data()
    data["important_events"] = [data["important_events"][0]] * 2

    with pytest.raises(ValidationError, match="distinct event clusters"):
        DailyNewsReport.model_validate(data)


def test_rejects_invented_cluster_reference_and_evidence_depth() -> None:
    report = DailyNewsReport.model_validate(report_data(cluster_id="event-9"))
    with pytest.raises(ValueError, match="Unsupported main event cluster"):
        validate_daily_news_report(
            report,
            allowed_references={"[Filing 1]"},
            allowed_main_clusters={"event-1"},
            evidence_depth_by_reference={
                "[Filing 1]": EvidenceDepth.OFFICIAL_FILING_EXTRACT
            },
        )


def test_prepares_profile_limitation_validates_and_saves_markdown(tmp_path: Path) -> None:
    profiles = InvestorProfileRepository(tmp_path / "profiles")
    profiles.update("SPCX", {"risk_tolerance": "low"})
    service = DailyNewsReportService(tmp_path, profiles)
    prepared = service.prepare(prepared_research())
    generated = GeneratedDailyNewsReport(
        DailyNewsReport.model_validate(report_data()),
        "gpt-test",
        TokenUsage(100, 200, 300),
    )
    reasoner = SimpleNamespace(generate=lambda evidence_json: generated)

    completed = service.generate_and_save(prepared, reasoner)

    assert '"status":"configured"' in prepared.request_json
    assert '"risk_tolerance":"low"' in prepared.request_json
    assert prepared.profile_field_count == 1
    assert completed.path == tmp_path / "2026-07-15" / "SPCX-news.md"
    assert "## 2. The 3 news events that actually matter" in completed.markdown
    assert "[Filing 1]" in completed.markdown
    assert "input=100, output=200, total=300" in completed.markdown
