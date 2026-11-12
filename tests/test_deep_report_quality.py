from typing import Any

import pytest

from investment_assistant.domain.deep_report import DeepResearchReport
from investment_assistant.services.deep_report_quality import (
    collect_deep_report_warnings,
    validate_deep_report_quality,
)
from investment_assistant.services.deep_report_repair import repair_deep_report
from tests.test_deep_report import deep_report_data


def evidence(content_extract: str | None = None) -> dict[str, Any]:
    return {
        "market_data": {
            "reference": "[Market Data]",
            "latest_close": "50.25",
            "daily_percentage_change": "2.5",
        },
        "market_context": {"thirty_day_change_percent": "8.4"},
        "fundamental_and_valuation_context": None,
        "ranked_information_items": [
            {
                "reference": "[Filing 1]",
                "headline": "SpaceX reports a material financing event",
                "content_extract": content_extract,
                "evidence_depth": "official filing extract",
            }
        ],
        "event_selection": {
            "main_event_clusters": [{"cluster_id": "event-1", "selected_reference": "[Filing 1]"}]
        },
    }


def validate(data: dict[str, Any], *, profile_fields: tuple[str, ...] = ()) -> None:
    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=evidence(),
        configured_profile_fields=profile_fields,
    )


def test_valid_beginner_report_passes_quality_gate() -> None:
    validate(deep_report_data())


def test_warns_for_generic_action_without_specific_follow_up() -> None:
    data = deep_report_data()
    data["investor_action"]["evidence_before_adding"] = ["Monitor developments."]
    data["investor_action"]["reason"] = "Monitor developments."

    report = DeepResearchReport.model_validate(data)
    warnings = collect_deep_report_warnings(
        report, evidence=evidence(), configured_profile_fields=()
    )

    assert any(issue.code == "investor_guidance" for issue in warnings)


def test_warns_when_guidance_ignores_configured_profile() -> None:
    data = deep_report_data()
    data["investor_action"]["reason"] = "Wait for clearer evidence."
    data["investor_action"]["evidence_before_adding"] = ["Verify the next filing."]

    report = DeepResearchReport.model_validate(data)
    warnings = collect_deep_report_warnings(
        report,
        evidence=evidence(),
        configured_profile_fields=("already_owns_stock",),
    )

    assert any("ignored the configured investor profile" in issue.message for issue in warnings)


def test_warns_for_headline_restatement_without_explanation() -> None:
    data = deep_report_data()
    data["key_evidence"][0]["fact"] = "SpaceX reports a material financing event."

    report = DeepResearchReport.model_validate(data)
    warnings = collect_deep_report_warnings(
        report, evidence=evidence(), configured_profile_fields=()
    )

    assert any(issue.code == "headline_restatement" for issue in warnings)


def test_rejects_unsupported_full_text_claim_and_repeated_uncertainty() -> None:
    data = deep_report_data()
    data["conclusion"]["summary"][0] = "After reading the article, the event appears material."

    with pytest.raises(ValueError, match="unsupported full-text access"):
        validate(data)

    data = deep_report_data()
    data["conclusion"]["summary"] = [
        "Insufficient evidence prevents a conclusion.",
        "Insufficient evidence remains.",
        "Insufficient evidence remains.",
        "Insufficient evidence remains.",
    ]

    report = DeepResearchReport.model_validate(data)
    warnings = collect_deep_report_warnings(
        report, evidence=evidence(), configured_profile_fields=()
    )

    assert any(issue.code == "repetitive_uncertainty" for issue in warnings)


def test_warns_for_unexplained_financial_jargon() -> None:
    data = deep_report_data()
    data["conclusion"]["summary"][0] = "Dilution could affect existing investors."

    report = DeepResearchReport.model_validate(data)
    warnings = collect_deep_report_warnings(
        report, evidence=evidence(), configured_profile_fields=()
    )

    assert any(issue.code == "beginner_language" for issue in warnings)


def test_rejects_number_absent_from_cited_evidence() -> None:
    data = deep_report_data()
    data["key_evidence"][0]["fact"] = "The filing reports 999 million in proceeds."

    with pytest.raises(ValueError, match="numerical fact is absent"):
        validate(data)


def test_rejects_excluded_third_party_fundamental_as_key_evidence() -> None:
    data = deep_report_data()
    data["key_evidence"][1]["fact"] = "Third-party revenue was 14.2 billion."
    supplied = evidence()
    supplied["fundamental_and_valuation_context"] = {
        "decision_grade_metrics": [],
        "excluded_non_decision_grade_metrics": [
            {"name": "revenue", "reason": "missing reporting period"}
        ],
    }

    with pytest.raises(ValueError, match="excluded third-party"):
        validate_deep_report_quality(
            DeepResearchReport.model_validate(data),
            evidence=supplied,
            configured_profile_fields=(),
        )


def test_rejects_weakened_thesis_based_only_on_unverified_fundamentals() -> None:
    data = deep_report_data()
    data["long_term_thesis"]["status"] = "weakened"
    data["key_evidence"] = [
        {
            "reference": "[Market Data]",
            "fact": "No decision-grade financial statements are available.",
            "why_it_matters": "The long-term financial picture cannot be verified.",
            "impact_direction": "neutral",
            "time_horizon": "long_term",
            "confidence": "low",
        }
    ]
    supplied = evidence()
    supplied["fundamental_and_valuation_context"] = {
        "decision_grade_metrics": [],
        "excluded_non_decision_grade_metrics": [{"name": "revenue"}],
    }

    with pytest.raises(ValueError, match="cannot be weakened mainly"):
        validate_deep_report_quality(
            DeepResearchReport.model_validate(data),
            evidence=supplied,
            configured_profile_fields=(),
        )


def test_allows_weakened_thesis_when_official_filing_supports_it() -> None:
    data = deep_report_data()
    data["long_term_thesis"]["status"] = "weakened"
    data["key_evidence"][0]["impact_direction"] = "negative"
    data["key_evidence"][0]["time_horizon"] = "long_term"
    supplied = evidence()
    supplied["fundamental_and_valuation_context"] = {
        "decision_grade_metrics": [],
        "excluded_non_decision_grade_metrics": [{"name": "revenue"}],
    }

    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=supplied,
        configured_profile_fields=(),
    )


def test_accepts_canonical_price_and_percentage_rounding() -> None:
    data = deep_report_data()
    data["key_evidence"] = [
        {
            "reference": "[Market Data]",
            "fact": (
                "The price was 141.73, the daily change was 1.86%, "
                "and the five-day change was -5.18%."
            ),
            "why_it_matters": "These are ordinary human-readable market figures.",
            "impact_direction": "mixed",
            "time_horizon": "near_term",
            "confidence": "high",
        }
    ]
    supplied = evidence()
    supplied["market_data"] = {
        "latest_close": "141.72999572753906",
        "daily_percentage_change": "1.8614319025814997",
        "five_day_percentage_change": "-5.178300280960987",
    }

    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=supplied,
        configured_profile_fields=(),
    )


def test_accepts_unsigned_percentage_magnitude_after_down_direction() -> None:
    data = deep_report_data()
    data["key_evidence"] = [
        {
            "reference": "[Market Data]",
            "fact": "The stock was down 5.43% today and 14.67% over five days.",
            "why_it_matters": "This describes recent price weakness.",
            "impact_direction": "negative",
            "time_horizon": "near_term",
            "confidence": "high",
        }
    ]
    supplied = evidence()
    supplied["market_data"] = {
        "daily_percentage_change": "-5.425121",
        "five_day_percentage_change": "-14.666211",
    }

    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=supplied,
        configured_profile_fields=(),
    )


def test_accepts_market_period_labels_and_below_direction() -> None:
    data = deep_report_data()
    data["key_evidence"] = [
        {
            "reference": "[Market Data]",
            "fact": (
                "The stock rose 18.23% over 30 days and 25.41% over 90 days; "
                "it was 0.14% below its 30-day high."
            ),
            "why_it_matters": "This describes trend and position within the recent range.",
            "impact_direction": "mixed",
            "time_horizon": "near_term",
            "confidence": "high",
        }
    ]
    supplied = evidence()
    supplied["market_context"] = {
        "thirty_day_change_percent": "18.23",
        "ninety_day_change_percent": "25.41",
        "distance_from_thirty_day_high_percent": "-0.14",
    }

    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=supplied,
        configured_profile_fields=(),
    )


def test_materially_false_market_value_with_period_label_remains_fatal() -> None:
    data = deep_report_data()
    data["key_evidence"][1]["fact"] = "The stock rose 99.99% over 30 days."
    supplied = evidence()
    supplied["market_context"] = {"thirty_day_change_percent": "18.23"}

    with pytest.raises(ValueError, match="99.99%"):
        validate_deep_report_quality(
            DeepResearchReport.model_validate(data),
            evidence=supplied,
            configured_profile_fields=(),
        )


def test_accepts_volume_percentage_below_average() -> None:
    data = deep_report_data()
    data["key_evidence"][1]["fact"] = (
        "Volume was 75.00% below the 20-day average volume of 166,880."
    )
    supplied = evidence()
    supplied["market_context"] = {
        "average_twenty_day_volume": 166880,
        "latest_volume_vs_average_percent": -75,
    }

    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=supplied,
        configured_profile_fields=(),
    )


@pytest.mark.parametrize("display", ["16.1 million", "16.11 million", "16,107,906"])
def test_accepts_safely_abbreviated_equivalent_volume(display: str) -> None:
    data = deep_report_data()
    data["key_evidence"][1]["fact"] = f"Trading volume was {display} shares."
    supplied = evidence()
    supplied["market_data"]["trading_volume"] = 16_107_906

    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=supplied,
        configured_profile_fields=(),
    )


def test_materially_false_rounded_market_value_remains_fatal() -> None:
    data = deep_report_data()
    data["key_evidence"][1]["fact"] = "The latest share price was 199.99."
    supplied = evidence()
    supplied["market_data"]["latest_close"] = "141.72999572753906"

    with pytest.raises(ValueError, match="199.99"):
        validate_deep_report_quality(
            DeepResearchReport.model_validate(data),
            evidence=supplied,
            configured_profile_fields=(),
        )


def test_filing_trailing_zeros_and_explicit_tranche_total_are_supported() -> None:
    data = deep_report_data()
    data["key_evidence"] = [
        {
            "reference": "[Filing 1]",
            "fact": "The notes total 25.0 billion with rates from 5.35% to 6.65%.",
            "why_it_matters": "This creates a material financing obligation.",
            "impact_direction": "mixed",
            "time_horizon": "long_term",
            "confidence": "high",
        }
    ]
    supplied = evidence()
    supplied["ranked_information_items"][0]["filing_metadata"] = {
        "material_event_summary": (
            "$7.0 billion at 5.350%, $6.0 billion, $6.0 billion, "
            "$2.5 billion, and $3.5 billion at 6.650%."
        )
    }

    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=supplied,
        configured_profile_fields=(),
    )


def test_filing_price_rounded_to_two_decimals_is_supported() -> None:
    data = deep_report_data()
    data["key_evidence"][0]["fact"] = "The reported transaction price was 105.32."
    supplied = evidence()
    supplied["ranked_information_items"][0]["content_extract"] = (
        "The reported transaction price was 105.318."
    )

    validate_deep_report_quality(
        DeepResearchReport.model_validate(data),
        evidence=supplied,
        configured_profile_fields=(),
    )


def test_unsupported_filing_fact_remains_fatal_after_scope_repair() -> None:
    data = deep_report_data()
    data["key_evidence"][0]["fact"] = "The complete filing shows proceeds of 999 billion."
    supplied = evidence("The supplied filing extract states proceeds of 25 billion.")
    report = DeepResearchReport.model_validate(data)

    repaired = repair_deep_report(report, evidence=supplied)

    with pytest.raises(ValueError, match="numerical fact is absent"):
        validate_deep_report_quality(
            repaired.report,
            evidence=supplied,
            configured_profile_fields=(),
        )


def test_rejects_long_verbatim_extract() -> None:
    copied = (
        "The company completed a financing transaction and received substantial proceeds "
        "that management expects to use for operating costs capital investment and general "
        "corporate purposes over the coming year."
    )
    data = deep_report_data()
    data["conclusion"]["summary"][0] = copied
    report = DeepResearchReport.model_validate(data)

    with pytest.raises(ValueError, match="reproduces a long passage"):
        validate_deep_report_quality(
            report,
            evidence=evidence(copied),
            configured_profile_fields=(),
        )
