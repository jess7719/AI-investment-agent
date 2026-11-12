"""Prepare, validate, render, and save one daily event-based news report."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from investment_assistant.adapters.investor_profiles import InvestorProfileRepository
from investment_assistant.domain.evidence import EvidenceDepth
from investment_assistant.domain.news_report import (
    CitedPoint,
    GeneratedDailyNewsReport,
    ImportantNewsEvent,
    validate_daily_news_report,
)
from investment_assistant.ports.news_report import DailyNewsReasoner
from investment_assistant.ports.research import MalformedResearchResponseError
from investment_assistant.domain.profile import InvestorProfile
from investment_assistant.services.research_service import PreparedResearch


@dataclass(frozen=True, slots=True)
class PreparedDailyNewsReport:
    research: PreparedResearch
    request_json: str
    allowed_main_clusters: frozenset[str]
    evidence_depth_by_reference: dict[str, EvidenceDepth]
    profile_configured: bool
    profile_field_count: int

    @property
    def character_count(self) -> int:
        return len(self.request_json)

    @property
    def approximate_tokens(self) -> int:
        return max(1, round(self.character_count / 4))


@dataclass(frozen=True, slots=True)
class CompletedDailyNewsReport:
    generated: GeneratedDailyNewsReport
    markdown: str
    path: Path


class DailyNewsReportService:
    def __init__(
        self,
        reports_root: Path,
        profiles: InvestorProfileRepository | None = None,
    ) -> None:
        self._reports_root = reports_root
        self._profiles = profiles

    def report_path(self, ticker: str, report_date: object) -> Path:
        return self._reports_root / str(report_date) / f"{ticker.upper()}-news.md"

    def prepare(self, research: PreparedResearch) -> PreparedDailyNewsReport:
        payload = dict(research.evidence)
        profile = (
            self._profiles.load(research.ticker)
            if self._profiles
            else InvestorProfile(ticker=research.ticker)
        )
        payload["report_request"] = {
            "report_type": "beginner-friendly daily event news report",
            "investor_profile": profile.evidence_payload(),
        }
        items = payload.get("ranked_information_items", [])
        depth_by_reference: dict[str, EvidenceDepth] = {}
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                reference = item.get("reference")
                depth = item.get("evidence_depth")
                if isinstance(reference, str) and isinstance(depth, str):
                    depth_by_reference[reference] = EvidenceDepth(depth)
        selection = payload.get("event_selection")
        main_clusters: set[str] = set()
        if isinstance(selection, dict):
            clusters = selection.get("main_event_clusters", [])
            if isinstance(clusters, list):
                main_clusters = {
                    str(cluster["cluster_id"])
                    for cluster in clusters
                    if isinstance(cluster, dict) and cluster.get("cluster_id")
                }
        return PreparedDailyNewsReport(
            research=research,
            request_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            allowed_main_clusters=frozenset(main_clusters),
            evidence_depth_by_reference=depth_by_reference,
            profile_configured=profile.is_configured,
            profile_field_count=len(profile.configured_fields),
        )

    def generate_and_save(
        self,
        prepared: PreparedDailyNewsReport,
        reasoner: DailyNewsReasoner,
    ) -> CompletedDailyNewsReport:
        generated = reasoner.generate(prepared.request_json)
        try:
            validate_daily_news_report(
                generated.report,
                allowed_references=set(prepared.research.allowed_references),
                allowed_main_clusters=set(prepared.allowed_main_clusters),
                evidence_depth_by_reference=prepared.evidence_depth_by_reference,
            )
        except ValueError as error:
            raise MalformedResearchResponseError(
                f"OpenAI daily news report failed evidence validation: {error}"
            ) from error
        markdown = render_daily_news_report(prepared, generated)
        path = self.report_path(
            prepared.research.ticker,
            prepared.research.report_date.isoformat(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        return CompletedDailyNewsReport(generated, markdown, path)


def render_daily_news_report(
    prepared: PreparedDailyNewsReport,
    generated: GeneratedDailyNewsReport,
) -> str:
    report = generated.report
    research = prepared.research
    lines = [
        f"# Daily news report: {research.ticker}",
        "",
        f"- Report date: {research.report_date.isoformat()}",
        f"- Model: {generated.model}",
        f"- Token usage: {_usage_text(generated)}",
        "- Evidence only: this report uses the collected extracts and metadata listed below.",
        "",
        "## 1. Today’s news in plain English",
        "",
    ]
    for point in report.todays_news_in_plain_english.sentences:
        lines.append(_point(point))
    lines.extend(
        [
            "",
            f"**Overall news flow: {report.todays_news_in_plain_english.overall_news_flow.value}.**",
            "",
            "## 2. The 3 news events that actually matter",
            "",
        ]
    )
    for event in report.important_events:
        _add_event(lines, event)
    lines.extend(["## 3. What is noise and can be ignored?", ""])
    if report.noise_to_ignore:
        for item in report.noise_to_ignore:
            lines.append(
                f"- **{item.title_or_theme}** — {item.why_low_value} "
                f"{' '.join(item.references)}"
            )
    else:
        lines.append("No low-value item was included merely to fill this section.")
    overall = report.overall_effect
    lines.extend(
        [
            "",
            f"## 4. Overall effect on {research.ticker}",
            "",
            f"- Business case: **{overall.business_case_effect.value}**",
            f"- Main effect: **{overall.primary_effect.value}**",
            f"- Share-price reaction: **{overall.price_reaction_assessment.value}**",
            f"- Most plausible interpretation: {_point(overall.most_plausible_market_interpretation)}",
        ]
    )
    for point in overall.explanation:
        lines.append(f"- {_point(point)}")
    lines.extend(
        [
            f"- Final stance: **{overall.final_stance.value}**",
            "",
            "## 5. What should I do with this information?",
            "",
            f"Profile basis: {report.investor_action.profile_basis}",
            "",
            "**If you already own SPCX:**",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report.investor_action.if_already_owns)
    lines.extend(["", "**If you do not own SPCX:**", ""])
    lines.extend(f"- {item}" for item in report.investor_action.if_does_not_own)
    lines.extend(
        [
            "",
            f"**Specific action trigger:** {report.investor_action.specific_action_trigger}",
            "",
            "## 6. What to watch next",
            "",
        ]
    )
    for item in report.what_to_watch_next:
        references = " ".join(item.references)
        lines.extend(
            [
                f"- **{item.event_or_data}** ({item.timeframe}) {references}",
                f"  - Positive result: {item.positive_result_means}",
                f"  - Negative result: {item.negative_result_means}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _add_event(lines: list[str], event: ImportantNewsEvent) -> None:
    lines.extend(
        [
            f"### {event.title}",
            "",
            "**What happened?**",
            "",
            _point(event.what_happened),
            "",
        ]
    )
    for fact in event.key_facts:
        lines.append(f"- {_point(fact)}")
    lines.extend(["", "**Why does it matter?**", ""])
    for point in event.why_it_matters:
        lines.append(f"- {_point(point)}")
    lines.extend(
        [
            "",
            f"**Likely stock effect:** {event.likely_stock_effect.value}; "
            f"time horizon: {event.time_horizon.value}.",
            "",
            f"**How strong is the evidence?** {event.evidence_strength.value}. "
            f"{event.evidence_reason} Evidence type: {event.evidence_type.value}.",
            "",
            f"**What should I take from this?** {event.beginner_takeaway}",
            "",
        ]
    )


def _point(point: CitedPoint) -> str:
    return f"{point.text} {' '.join(point.references)}"


def _usage_text(generated: GeneratedDailyNewsReport) -> str:
    usage = generated.usage
    if all(value is None for value in (usage.input_tokens, usage.output_tokens, usage.total_tokens)):
        return "not provided by API"
    return (
        f"input={usage.input_tokens if usage.input_tokens is not None else 'unknown'}, "
        f"output={usage.output_tokens if usage.output_tokens is not None else 'unknown'}, "
        f"total={usage.total_tokens if usage.total_tokens is not None else 'unknown'}"
    )
