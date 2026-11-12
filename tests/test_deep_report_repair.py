import re

from investment_assistant.domain.deep_report import DeepResearchReport
from investment_assistant.domain.report_validation import ValidationSeverity
from investment_assistant.services.deep_report_repair import repair_deep_report
from investment_assistant.services.deep_report_quality import validate_deep_report_quality
from tests.test_deep_report import deep_report_data


def long_sentence(word_count: int) -> str:
    words = [
        "The",
        "U.S.",
        "market",
        "price",
        "was",
        "141.72999572753906",
        "according",
        "to",
        "the",
        "supplied",
        "[Market Data]",
        "while",
        "the",
        "public",
        "source",
        "at",
        "https://example.com/research",
        "described",
        "the",
        "event",
    ]
    while len(words) < word_count:
        words.append("carefully")
    return " ".join(words) + "."


def test_repairs_long_sentence_and_precision_without_breaking_protected_text() -> None:
    data = deep_report_data()
    data["conclusion"]["summary"][0] = long_sentence(62)
    report = DeepResearchReport.model_validate(data)

    repaired = repair_deep_report(report)
    text = repaired.report.conclusion.summary[0]

    assert "141.73" in text
    assert "141.72999572753906" not in text
    assert "U.S." in text
    assert "[Market Data]" in text
    assert "https://example.com/research" in text
    assert len(re.split(r"(?<=[.!?])\s+", text)) >= 2
    assert any(issue.code == "long_sentence" for issue in repaired.issues)
    assert any(issue.code == "decimal_precision" for issue in repaired.issues)


def test_over_seventy_words_is_repaired_and_flagged_for_review() -> None:
    data = deep_report_data()
    data["conclusion"]["summary"][0] = " ".join(["Word"] * 72) + "."

    repaired = repair_deep_report(DeepResearchReport.model_validate(data))

    assert any(issue.code == "long_sentence" for issue in repaired.issues)
    assert any(
        issue.code == "long_sentence_review" and issue.severity is ValidationSeverity.WARNING
        for issue in repaired.issues
    )


def test_formats_volume_and_repairs_duplicate_bullets_and_markdown_prefix() -> None:
    data = deep_report_data()
    data["key_evidence"][1]["fact"] = "Volume was 141729995 shares."
    data["investor_action"]["evidence_before_adding"] = [
        "- Review the position size.",
        "- Review the position size.",
    ]

    repaired = repair_deep_report(DeepResearchReport.model_validate(data))

    assert repaired.report.key_evidence[1].fact == "Volume was 141,729,995 shares."
    assert repaired.report.investor_action.evidence_before_adding == ["Review the position size."]
    assert any(issue.code == "duplicate_list_item" for issue in repaired.issues)


def test_repairs_complete_filing_wording_and_retains_citation() -> None:
    data = deep_report_data()
    data["key_evidence"][0]["fact"] = "The complete filing shows proceeds of 25 billion."
    evidence = {
        "market_data": {},
        "market_context": None,
        "fundamental_and_valuation_context": None,
        "ranked_information_items": [
            {
                "reference": "[Filing 1]",
                "evidence_depth": "official filing extract",
                "content_extract": "The supplied filing extract states proceeds of 25 billion.",
            }
        ],
    }

    repaired = repair_deep_report(
        DeepResearchReport.model_validate(data),
        evidence=evidence,
    )

    assert repaired.report.key_evidence[0].reference == "[Filing 1]"
    assert repaired.report.key_evidence[0].fact == (
        "the supplied filing extract indicates proceeds of 25 billion."
    )
    assert any(issue.code == "evidence_scope_wording" for issue in repaired.issues)
    validate_deep_report_quality(
        repaired.report,
        evidence=evidence,
        configured_profile_fields=(),
    )


def test_repairs_negated_complete_filing_limitation_without_filing_evidence() -> None:
    data = deep_report_data()
    data["limitations"][0] = (
        "Only bounded extracts were supplied; no complete article body or complete filing "
        "was available."
    )

    repaired = repair_deep_report(DeepResearchReport.model_validate(data), evidence={})

    assert "complete filing" not in repaired.report.limitations[0].lower()
    assert "only bounded extracts were supplied" in repaired.report.limitations[0].lower()
    assert any(issue.code == "evidence_scope_wording" for issue in repaired.issues)
