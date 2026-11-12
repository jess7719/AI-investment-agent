from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from investment_assistant.adapters.investor_profiles import InvestorProfileRepository
from investment_assistant.adapters.openai_deep_report import (
    MalformedDeepAPIResponse,
    ReceivedMalformedDeepResponseError,
)
from investment_assistant.domain.deep_report import (
    DeepResearchReport,
    GeneratedDeepResearchReport,
    validate_deep_report,
)
from investment_assistant.domain.evidence import EvidenceDepth
from investment_assistant.domain.research import TokenUsage
from investment_assistant.ports.research import MalformedResearchResponseError
from investment_assistant.services.deep_report_service import DeepReportService
from investment_assistant.services.research_service import PreparedResearch


def deep_report_data(
    filing_reference: str = "[Filing 1]",
) -> dict[str, Any]:
    probability = {"low_percent": 40, "high_percent": 55}
    return {
        "conclusion": {
            "stance": "neutral",
            "summary": [
                "The current evidence does not clearly change the investment case.",
                "The filing matters more than the short-term price movement.",
            ],
            "most_important_reason": "The official filing confirms a material event.",
        },
        "key_evidence": [
            {
                "reference": filing_reference,
                "fact": "The filing discloses a material event.",
                "why_it_matters": "The event may affect future financing decisions.",
                "impact_direction": "mixed",
                "time_horizon": "medium_term",
                "confidence": "high",
            },
            {
                "reference": "[Market Data]",
                "fact": "The application supplied a recent market observation.",
                "why_it_matters": "It provides context but does not prove a catalyst.",
                "impact_direction": "neutral",
                "time_horizon": "near_term",
                "confidence": "medium",
            },
        ],
        "near_term_view": {
            "base_case": {
                "summary": "The stock remains volatile while investors assess the filing.",
                "probability": probability,
            },
            "bull_case": {
                "summary": "Sentiment improves if the event supports productive growth.",
                "probability": {"low_percent": 20, "high_percent": 35},
            },
            "bear_case": {
                "summary": "The stock weakens if investors see added financial risk.",
                "probability": {"low_percent": 20, "high_percent": 35},
            },
            "main_driver": "How investors interpret the filing's business effect.",
            "what_would_change_the_view": "Clearer official financial evidence would change the view.",
        },
        "medium_term_view": {
            "base_case": "Investors continue balancing growth potential against financial risk.",
            "bull_case": "Operating progress makes the event look constructive.",
            "bear_case": "Weak cash generation makes the event more concerning.",
            "main_catalysts": ["The next official financial update."],
            "main_risks": ["Financial pressure without operating improvement."],
            "what_would_change_the_view": "Verified financial results would change the assessment.",
        },
        "long_term_thesis": {
            "status": "unchanged",
            "core_thesis": "Long-term value depends on turning growth into durable cash generation.",
            "supporting_factors": ["The company retains meaningful growth potential."],
            "threats": ["Financial obligations may rise faster than cash generation."],
            "thesis_invalidation_conditions": [
                "Persistent financial pressure without operating progress."
            ],
        },
        "investor_action": {
            "action_for_current_position": "hold_and_monitor",
            "whether_to_add_more_now": "wait",
            "evidence_before_adding": ["Verify cash generation in an official disclosure."],
            "reason": "Keep the current position small while checking the original thesis.",
        },
        "thesis_triggers": {
            "positive_triggers": ["Verified operating improvement."],
            "negative_triggers": ["Rising obligations without better cash generation."],
            "upcoming_events_to_monitor": ["The next official financial disclosure."],
        },
        "confidence": {
            "data": {"level": "medium", "reason": "Market observations are supplied."},
            "catalyst_interpretation": {
                "level": "medium",
                "reason": "The filing confirms an event but not its stock-price effect.",
            },
            "fundamentals": {
                "level": "low",
                "reason": "Complete verified financial statements are unavailable.",
            },
            "near_term_forecast": {
                "level": "low",
                "reason": "Short-term market reactions remain uncertain.",
            },
            "long_term_thesis": {
                "level": "medium",
                "reason": "The core thesis is clear but important evidence is missing.",
            },
        },
        "limitations": ["Complete verified financial statements are unavailable."],
    }


def prepared_research() -> PreparedResearch:
    evidence = {
        "ranked_information_items": [
            {
                "reference": "[Filing 1]",
                "headline": "SpaceX reports a material financing event",
                "content_extract": "The filing discloses a material event.",
                "evidence_depth": "official filing extract",
                "selection_role": "main",
            }
        ],
        "event_selection": {
            "main_event_clusters": [{"cluster_id": "event-1", "selected_reference": "[Filing 1]"}]
        },
    }
    return PreparedResearch(
        ticker="SPCX",
        report_date=date(2026, 7, 16),
        evidence=evidence,
        evidence_json="{}",
        allowed_references=frozenset({"[Market Data]", "[Filing 1]"}),
        news_count=0,
        filing_count=1,
    )


def test_rejects_reversed_probability_and_too_many_evidence_items() -> None:
    data = deep_report_data()
    data["near_term_view"]["base_case"]["probability"] = {
        "low_percent": 70,
        "high_percent": 30,
    }
    with pytest.raises(ValidationError, match="Probability range is reversed"):
        DeepResearchReport.model_validate(data)

    data = deep_report_data()
    data["key_evidence"] = data["key_evidence"] * 3
    with pytest.raises(ValidationError):
        DeepResearchReport.model_validate(data)


def test_compact_fixture_has_nine_sections_and_stays_under_target() -> None:
    report = DeepResearchReport.model_validate(deep_report_data())

    assert len(DeepResearchReport.model_fields) == 9
    assert len(report.key_evidence) <= 5
    assert (len(report.model_dump_json()) + 3) // 4 < 3_500


def test_rejects_invented_reference() -> None:
    report = DeepResearchReport.model_validate(deep_report_data("[Filing 9]"))
    with pytest.raises(ValueError, match="Unsupported evidence reference"):
        validate_deep_report(
            report,
            allowed_references={"[Market Data]", "[Filing 1]"},
            allowed_main_clusters={"event-1"},
            evidence_depth_by_reference={"[Filing 1]": EvidenceDepth.OFFICIAL_FILING_EXTRACT},
        )


def test_prepares_validates_renders_and_saves_compact_report(tmp_path: Path) -> None:
    profiles = InvestorProfileRepository(tmp_path / "profiles")
    profiles.update(
        "SPCX",
        {
            "already_owns_stock": True,
            "average_purchase_price": 140,
            "amount_invested": 50,
            "intended_holding_period": "long term",
            "investment_objective": "long-term capital growth",
            "reason_for_investing": "small starter position",
            "position_description": "intentionally small starter position",
            "trading_preference": "does not want frequent short-term trading",
            "investment_currency": "GBP",
        },
    )
    service = DeepReportService(tmp_path, profiles)
    prepared = service.prepare(prepared_research())
    report = DeepResearchReport.model_validate(deep_report_data())
    generated = GeneratedDeepResearchReport(
        report,
        "gpt-test",
        TokenUsage(2000, 1200, 3200, 500),
        response_id="resp_success",
        request_timestamp=datetime(2026, 7, 16, 11, 30, tzinfo=timezone.utc),
        raw_output=report.model_dump_json(),
        response_status="completed",
    )

    completed = service.generate_and_save(
        prepared, SimpleNamespace(generate=lambda evidence_json: generated)
    )

    assert '"status":"configured"' in prepared.request_json
    assert '"average_purchase_price":"140"' in prepared.request_json
    assert prepared.profile_field_count == 9
    assert '"investment_currency":"GBP"' in prepared.request_json
    assert '"trading_preference":"does not want frequent short-term trading"' in (
        prepared.request_json
    )
    assert completed.path == tmp_path / "2026-07-16" / "SPCX-deep.md"
    assert "## 1. Conclusion" in completed.markdown
    assert "## 2. Key evidence" in completed.markdown
    assert "**Base case (40-55%):**" in completed.markdown
    assert "## 6. Investor action" in completed.markdown
    assert "## 8. Confidence" in completed.markdown
    assert "input=2000, cached_input=500, output=1200, total=3200" in completed.markdown
    structured = completed.structured_path.read_text(encoding="utf-8")
    assert '"response_id": "resp_success"' in structured
    assert '"response_status": "completed"' in structured
    assert '"raw_output": "{\\"conclusion\\"' in structured


def test_fatal_failure_preserves_rejected_response_and_usage(tmp_path: Path) -> None:
    service = DeepReportService(tmp_path)
    prepared = service.prepare(prepared_research())
    data = deep_report_data()
    data["conclusion"]["summary"][0] = "After reading the article, the event appears material."
    generated = GeneratedDeepResearchReport(
        DeepResearchReport.model_validate(data),
        "gpt-test",
        TokenUsage(2000, 1200, 3200, 500),
    )

    with pytest.raises(MalformedResearchResponseError, match="retained") as caught:
        service.generate_and_save(
            prepared, SimpleNamespace(generate=lambda evidence_json: generated)
        )

    saved = caught.value.rejected_path.read_text(encoding="utf-8")
    assert '"input_tokens": 2000' in saved
    assert '"cached_input_tokens": 500' in saved
    assert '"approved_for_investment_use": false' in saved
    assert not (tmp_path / "2026-07-16" / "SPCX-deep.md").exists()


def test_malformed_api_response_preserves_raw_output_and_metadata(tmp_path: Path) -> None:
    service = DeepReportService(tmp_path)
    prepared = service.prepare(prepared_research())
    receipt = MalformedDeepAPIResponse(
        model="gpt-test",
        usage=TokenUsage(2200, 50, 2250, 500),
        response_id="resp_malformed",
        request_timestamp=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        raw_output='{"broken":true}',
        response_status="incomplete",
        incomplete_details={"reason": "max_output_tokens"},
    )

    def malformed(evidence_json: str) -> None:
        raise ReceivedMalformedDeepResponseError(receipt)

    with pytest.raises(MalformedResearchResponseError, match="malformed") as caught:
        service.generate_and_save(prepared, SimpleNamespace(generate=malformed))

    saved = caught.value.rejected_path.read_text(encoding="utf-8")
    assert '"response_id": "resp_malformed"' in saved
    assert '"response_status": "incomplete"' in saved
    assert '"reason": "max_output_tokens"' in saved
    assert '"raw_output": "{\\"broken\\":true}"' in saved


def test_simulated_long_sentence_is_repaired_not_discarded(tmp_path: Path) -> None:
    service = DeepReportService(tmp_path)
    prepared = service.prepare(prepared_research())
    data = deep_report_data()
    words = ["Evidence"] * 58
    words.insert(6, "141.72999572753906")
    data["conclusion"]["summary"][0] = " ".join(words) + "."
    report = DeepResearchReport.model_validate(data)
    generated = GeneratedDeepResearchReport(
        report=report,
        model="gpt-test",
        usage=TokenUsage(2100, 1300, 3400, 400),
        response_id="resp_fixture_long_sentence",
        request_timestamp=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        raw_output=report.model_dump_json(),
    )

    completed = service.generate_and_save(
        prepared, SimpleNamespace(generate=lambda evidence_json: generated)
    )

    assert completed.validation.status.value == "repaired"
    assert "141.73" in completed.markdown
    assert "141.72999572753906" not in completed.markdown
    sidecar = completed.structured_path.read_text(encoding="utf-8")
    assert "141.72999572753906" in sidecar
