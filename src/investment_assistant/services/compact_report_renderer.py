"""Markdown rendering for the compact deep-report schema."""

from __future__ import annotations

from investment_assistant.domain.deep_report import (
    GeneratedDeepResearchReport,
    NearTermScenario,
)


def render_compact_deep_report(
    *,
    ticker: str,
    report_date: str,
    generated: GeneratedDeepResearchReport,
    usage_text: str,
) -> str:
    report = generated.report
    lines = [
        f"# Deep investment research report: {ticker}",
        "",
        f"- Report date: {report_date}",
        f"- Model: {generated.model}",
        f"- Token usage: {usage_text}",
        "- Evidence-grounded research support; no trade is executed.",
        "",
        "## 1. Conclusion",
        "",
        f"**Stance: {_label(report.conclusion.stance.value)}.**",
        "",
        *report.conclusion.summary,
        "",
        f"**Most important reason:** {report.conclusion.most_important_reason}",
        "",
        "## 2. Key evidence",
        "",
    ]
    if report.key_evidence:
        for index, item in enumerate(report.key_evidence, start=1):
            lines.extend(
                [
                    f"### Evidence {index} {item.reference}",
                    "",
                    f"- **Fact:** {item.fact}",
                    f"- **Why it matters:** {item.why_it_matters}",
                    f"- **Impact:** {_label(item.impact_direction.value)}",
                    f"- **Horizon:** {_label(item.time_horizon.value)}",
                    f"- **Confidence:** {item.confidence.value}",
                    "",
                ]
            )
    else:
        lines.extend(["No evidence item met the selection threshold.", ""])

    near = report.near_term_view
    lines.extend(
        [
            "## 3. Near-term view: next 1-2 weeks",
            "",
            "_Probability ranges are subjective analytical estimates, not guarantees._",
            "",
        ]
    )
    _add_near_scenario(lines, "Base case", near.base_case)
    _add_near_scenario(lines, "Bull case", near.bull_case)
    _add_near_scenario(lines, "Bear case", near.bear_case)
    lines.extend(
        [
            f"- **Main driver:** {near.main_driver}",
            f"- **What would change the view:** {near.what_would_change_the_view}",
            "",
        ]
    )

    medium = report.medium_term_view
    lines.extend(
        [
            "## 4. Medium-term view: next 1-3 months",
            "",
            f"- **Base case:** {medium.base_case}",
            f"- **Bull case:** {medium.bull_case}",
            f"- **Bear case:** {medium.bear_case}",
            "",
            "**Main catalysts:**",
            "",
        ]
    )
    _add_bullets(lines, medium.main_catalysts)
    lines.extend(["", "**Main risks:**", ""])
    _add_bullets(lines, medium.main_risks)
    lines.extend(
        [
            "",
            f"**What would change the view:** {medium.what_would_change_the_view}",
            "",
        ]
    )

    thesis = report.long_term_thesis
    lines.extend(
        [
            "## 5. Long-term thesis",
            "",
            f"**Status: {_label(thesis.status.value)}.**",
            "",
            thesis.core_thesis,
            "",
            "**Supporting factors:**",
            "",
        ]
    )
    _add_bullets(lines, thesis.supporting_factors)
    lines.extend(["", "**Threats:**", ""])
    _add_bullets(lines, thesis.threats)
    lines.extend(["", "**Thesis invalidation conditions:**", ""])
    _add_bullets(lines, thesis.thesis_invalidation_conditions)

    action = report.investor_action
    lines.extend(
        [
            "",
            "## 6. Investor action",
            "",
            f"- **Current position:** {_label(action.action_for_current_position.value)}",
            f"- **Add more now:** {_label(action.whether_to_add_more_now.value)}",
            f"- **Reason:** {action.reason}",
            "",
            "**Evidence to see before adding:**",
            "",
        ]
    )
    _add_bullets(lines, action.evidence_before_adding)

    triggers = report.thesis_triggers
    lines.extend(["", "## 7. Thesis triggers", "", "**Positive triggers:**", ""])
    _add_bullets(lines, triggers.positive_triggers)
    lines.extend(["", "**Negative triggers:**", ""])
    _add_bullets(lines, triggers.negative_triggers)
    lines.extend(["", "**Upcoming events to monitor:**", ""])
    _add_bullets(lines, triggers.upcoming_events_to_monitor)

    confidence = report.confidence
    lines.extend(
        [
            "",
            "## 8. Confidence",
            "",
            "These levels describe evidence quality, not certainty about future prices.",
            "",
        ]
    )
    for label, assessment in (
        ("Data", confidence.data),
        ("Catalyst interpretation", confidence.catalyst_interpretation),
        ("Fundamentals", confidence.fundamentals),
        ("Near-term forecast", confidence.near_term_forecast),
        ("Long-term thesis", confidence.long_term_thesis),
    ):
        lines.append(f"- **{label}: {assessment.level.value}.** {assessment.reason}")

    lines.extend(["", "## 9. Limitations", ""])
    if report.limitations:
        _add_bullets(lines, report.limitations)
    else:
        lines.append("- No material limitation was identified in the supplied dataset.")
    return "\n".join(lines).rstrip() + "\n"


def _add_near_scenario(
    lines: list[str],
    label: str,
    scenario: NearTermScenario,
) -> None:
    probability = f"{scenario.probability.low_percent}-{scenario.probability.high_percent}%"
    lines.append(f"- **{label} ({probability}):** {scenario.summary}")


def _add_bullets(lines: list[str], values: list[str]) -> None:
    if values:
        lines.extend(f"- {value}" for value in values)
    else:
        lines.append("- None identified from the supplied evidence.")


def _label(value: str) -> str:
    return value.replace("_", " ")
