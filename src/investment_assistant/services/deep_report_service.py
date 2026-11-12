"""Prepare, validate, render, and save a beginner-friendly deep report."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path

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
from investment_assistant.domain.profile import InvestorProfile
from investment_assistant.domain.report_validation import (
    ValidationIssue,
    ValidationSeverity,
    ValidationStatus,
    ValidationSummary,
)
from investment_assistant.domain.research import TokenUsage
from investment_assistant.ports.deep_report import DeepResearchReasoner
from investment_assistant.ports.research import MalformedResearchResponseError
from investment_assistant.services.deep_report_quality import (
    collect_deep_report_warnings,
    validate_deep_report_quality,
)
from investment_assistant.services.deep_report_artifacts import (
    DeepReportArtifactStore,
    load_response_envelope,
)
from investment_assistant.services.deep_report_repair import repair_deep_report
from investment_assistant.services.compact_report_renderer import (
    render_compact_deep_report,
)
from investment_assistant.services.research_service import PreparedResearch


@dataclass(frozen=True, slots=True)
class PreparedDeepResearch:
    research: PreparedResearch
    request_json: str
    allowed_main_clusters: frozenset[str]
    evidence_depth_by_reference: dict[str, EvidenceDepth]
    profile_configured: bool
    profile_field_count: int
    profile_configured_fields: tuple[str, ...]

    @property
    def character_count(self) -> int:
        return len(self.request_json)

    @property
    def approximate_tokens(self) -> int:
        return max(1, round(self.character_count / 4))


@dataclass(frozen=True, slots=True)
class CompletedDeepResearch:
    generated: GeneratedDeepResearchReport
    markdown: str
    path: Path
    structured_path: Path
    validation: ValidationSummary


class RejectedDeepReportError(MalformedResearchResponseError):
    def __init__(
        self,
        message: str,
        *,
        generated: GeneratedDeepResearchReport | MalformedDeepAPIResponse,
        rejected_path: Path,
        validation_path: Path,
        validation: ValidationSummary,
    ) -> None:
        super().__init__(message)
        self.generated = generated
        self.rejected_path = rejected_path
        self.validation_path = validation_path
        self.validation = validation


class SavedReportValidationError(MalformedResearchResponseError):
    def __init__(self, message: str, validation: ValidationSummary) -> None:
        super().__init__(message)
        self.validation = validation


class DeepReportService:
    def __init__(
        self,
        reports_root: Path,
        profiles: InvestorProfileRepository | None = None,
    ) -> None:
        self._reports_root = reports_root
        self._profiles = profiles
        self._artifacts = DeepReportArtifactStore(reports_root)

    def report_path(self, ticker: str, report_date: object) -> Path:
        return self._reports_root / str(report_date) / f"{ticker.upper()}-deep.md"

    def prepare(self, research: PreparedResearch) -> PreparedDeepResearch:
        payload = dict(research.evidence)
        payload.pop("raw_numeric_evidence", None)
        scope = payload.get("scope")
        asset_type = (
            str(scope.get("asset_type"))
            if isinstance(scope, dict) and scope.get("asset_type")
            else "equity"
        )
        profile = (
            self._profiles.load(research.ticker)
            if self._profiles
            else InvestorProfile(ticker=research.ticker)
        )
        payload["report_request"] = {
            "report_type": "compact decision-useful deep investment research report",
            "required_horizons": ["next 1-2 weeks", "next 1-3 months", "long term"],
            "probabilities": "subjective analytical ranges, not predictions",
            "target_output_tokens": 3500,
            "maximum_key_evidence_items": 5,
            "investor_profile": profile.evidence_payload(),
            "asset_type": asset_type,
            "analysis_framework": (
                {
                    "primary_subject": "Brent crude exposure through a commodity ETC",
                    "valuation_replacement": [
                        "underlying commodity context",
                        "futures curve and roll yield",
                        "tracking quality",
                        "product cost",
                    ],
                    "long_term_questions": [
                        "structural oil supply and demand",
                        "whether the futures curve helps or hurts a holder",
                        "roll-yield risk",
                        "Brent upside and downside drivers",
                        "how efficiently the ETC captures Brent returns",
                    ],
                    "equity_metrics_not_applicable": [
                        "revenue",
                        "net income",
                        "operating margin",
                        "P/E",
                        "company free cash flow",
                        "company balance-sheet valuation",
                    ],
                }
                if asset_type == "commodity_etp"
                else {"primary_subject": "company equity"}
            ),
        }
        depth_by_reference: dict[str, EvidenceDepth] = {}
        items = payload.get("ranked_information_items", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                reference = item.get("reference")
                depth = item.get("evidence_depth")
                if isinstance(reference, str) and isinstance(depth, str):
                    depth_by_reference[reference] = EvidenceDepth(depth)
        main_clusters: set[str] = set()
        selection = payload.get("event_selection")
        if isinstance(selection, dict):
            clusters = selection.get("main_event_clusters", [])
            if isinstance(clusters, list):
                main_clusters = {
                    str(cluster["cluster_id"])
                    for cluster in clusters
                    if isinstance(cluster, dict) and cluster.get("cluster_id")
                }
        return PreparedDeepResearch(
            research=research,
            request_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            allowed_main_clusters=frozenset(main_clusters),
            evidence_depth_by_reference=depth_by_reference,
            profile_configured=profile.is_configured,
            profile_field_count=len(profile.configured_fields),
            profile_configured_fields=profile.configured_fields,
        )

    def generate_and_save(
        self,
        prepared: PreparedDeepResearch,
        reasoner: DeepResearchReasoner,
    ) -> CompletedDeepResearch:
        report_date = prepared.research.report_date.isoformat()
        try:
            generated = reasoner.generate(prepared.request_json)
        except ReceivedMalformedDeepResponseError as error:
            validation = ValidationSummary(
                status=ValidationStatus.REJECTED,
                issues=[
                    ValidationIssue(
                        code="malformed_structured_output",
                        severity=ValidationSeverity.FATAL,
                        message=(
                            "The API response was received and retained, but it did not "
                            "match the mandatory deep-report schema."
                        ),
                    )
                ],
            )
            rejected_path, validation_path = self._artifacts.reject_malformed(
                ticker=prepared.research.ticker,
                report_date=report_date,
                receipt=error.receipt,
                validation=validation,
            )
            raise RejectedDeepReportError(
                "OpenAI response was retained but rejected as malformed.",
                generated=error.receipt,
                rejected_path=rejected_path,
                validation_path=validation_path,
                validation=validation,
            ) from error
        pending = self._artifacts.preserve_received(
            ticker=prepared.research.ticker,
            report_date=report_date,
            generated=generated,
            evidence=prepared.research.evidence,
            allowed_references=prepared.research.allowed_references,
            allowed_main_clusters=prepared.allowed_main_clusters,
            evidence_depth_by_reference=prepared.evidence_depth_by_reference,
            configured_profile_fields=prepared.profile_configured_fields,
        )
        issues: list[ValidationIssue] = []
        try:
            validate_deep_report(
                generated.report,
                allowed_references=set(prepared.research.allowed_references),
                allowed_main_clusters=set(prepared.allowed_main_clusters),
                evidence_depth_by_reference=prepared.evidence_depth_by_reference,
            )
        except ValueError as error:
            issues.append(
                ValidationIssue(
                    code="evidence_validation",
                    severity=ValidationSeverity.FATAL,
                    message=str(error),
                )
            )

        repaired = repair_deep_report(
            generated.report,
            evidence=prepared.research.evidence,
        )
        issues.extend(repaired.issues)
        if not any(issue.severity is ValidationSeverity.FATAL for issue in issues):
            try:
                validate_deep_report_quality(
                    repaired.report,
                    evidence=prepared.research.evidence,
                    configured_profile_fields=prepared.profile_configured_fields,
                )
            except ValueError as error:
                issues.append(
                    ValidationIssue(
                        code="fatal_quality_validation",
                        severity=ValidationSeverity.FATAL,
                        message=str(error),
                    )
                )
        issues.extend(
            collect_deep_report_warnings(
                repaired.report,
                evidence=prepared.research.evidence,
                configured_profile_fields=prepared.profile_configured_fields,
            )
        )
        fatal = [issue for issue in issues if issue.severity is ValidationSeverity.FATAL]
        status = (
            ValidationStatus.REJECTED
            if fatal
            else (
                ValidationStatus.REPAIRED
                if any(issue.severity is ValidationSeverity.REPAIRABLE for issue in issues)
                else ValidationStatus.PASSED
            )
        )
        validation = ValidationSummary(status=status, issues=issues)
        if fatal:
            rejected_path, validation_path = self._artifacts.reject(
                pending,
                ticker=prepared.research.ticker,
                report_date=report_date,
                validation=validation,
            )
            raise RejectedDeepReportError(
                "OpenAI deep report was retained but rejected by fatal validation.",
                generated=generated,
                rejected_path=rejected_path,
                validation_path=validation_path,
                validation=validation,
            )
        repaired_generated = replace(generated, report=repaired.report)
        markdown = render_deep_report(prepared, repaired_generated)
        path = self.report_path(
            prepared.research.ticker,
            report_date,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        structured_path = self._artifacts.approve(
            pending,
            ticker=prepared.research.ticker,
            report_date=report_date,
            repaired_report=repaired.report,
            validation=validation,
        )
        return CompletedDeepResearch(
            repaired_generated,
            markdown,
            path,
            structured_path,
            validation,
        )

    def validate_saved_response(
        self,
        path: Path,
        *,
        markdown_path: Path | None = None,
    ) -> CompletedDeepResearch:
        """Validate, locally repair, and render a stored response without any API call."""

        try:
            envelope = load_response_envelope(path)
        except ValueError as error:
            raise SavedReportValidationError(
                str(error),
                ValidationSummary(
                    status=ValidationStatus.REJECTED,
                    issues=[
                        ValidationIssue(
                            code="corrupted_json",
                            severity=ValidationSeverity.FATAL,
                            message=str(error),
                        )
                    ],
                ),
            ) from error
        report_value = envelope.get("report", envelope)
        try:
            report = DeepResearchReport.model_validate(report_value)
        except ValidationError as error:
            validation = ValidationSummary(
                status=ValidationStatus.REJECTED,
                issues=[
                    ValidationIssue(
                        code="malformed_structured_output",
                        severity=ValidationSeverity.FATAL,
                        message="Stored response does not match the mandatory deep-report schema.",
                    )
                ],
            )
            raise SavedReportValidationError(
                "Stored response has malformed structured output.",
                validation,
            ) from error
        context = envelope.get("validation_context")
        if not isinstance(context, dict):
            validation = ValidationSummary(
                status=ValidationStatus.REJECTED,
                issues=[
                    ValidationIssue(
                        code="missing_validation_context",
                        severity=ValidationSeverity.FATAL,
                        message=(
                            "Stored response lacks evidence context, so citations cannot "
                            "be verified safely."
                        ),
                    )
                ],
            )
            raise SavedReportValidationError(
                "Stored response lacks validation context.",
                validation,
            )
        evidence = context.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        allowed_references = frozenset(
            str(value) for value in context.get("allowed_references", [])
        )
        allowed_clusters = frozenset(
            str(value) for value in context.get("allowed_main_clusters", [])
        )
        depth_values = context.get("evidence_depth_by_reference", {})
        depth_by_reference = (
            {str(reference): EvidenceDepth(str(depth)) for reference, depth in depth_values.items()}
            if isinstance(depth_values, dict)
            else {}
        )
        profile_fields = tuple(str(value) for value in context.get("configured_profile_fields", []))
        issues: list[ValidationIssue] = []
        try:
            validate_deep_report(
                report,
                allowed_references=set(allowed_references),
                allowed_main_clusters=set(allowed_clusters),
                evidence_depth_by_reference=depth_by_reference,
            )
        except ValueError as error:
            issues.append(
                ValidationIssue(
                    code="evidence_validation",
                    severity=ValidationSeverity.FATAL,
                    message=str(error),
                )
            )
        repaired = repair_deep_report(report, evidence=evidence)
        issues.extend(repaired.issues)
        if not any(issue.severity is ValidationSeverity.FATAL for issue in issues):
            try:
                validate_deep_report_quality(
                    repaired.report,
                    evidence=evidence,
                    configured_profile_fields=profile_fields,
                )
            except ValueError as error:
                issues.append(
                    ValidationIssue(
                        code="fatal_quality_validation",
                        severity=ValidationSeverity.FATAL,
                        message=str(error),
                    )
                )
        issues.extend(
            collect_deep_report_warnings(
                repaired.report,
                evidence=evidence,
                configured_profile_fields=profile_fields,
            )
        )
        fatal = [issue for issue in issues if issue.severity is ValidationSeverity.FATAL]
        validation = ValidationSummary(
            status=(
                ValidationStatus.REJECTED
                if fatal
                else (
                    ValidationStatus.REPAIRED
                    if any(issue.severity is ValidationSeverity.REPAIRABLE for issue in issues)
                    else ValidationStatus.PASSED
                )
            ),
            issues=issues,
        )
        if fatal:
            raise SavedReportValidationError(
                "Stored response failed fatal validation.",
                validation,
            )
        ticker = str(envelope.get("ticker") or "UNKNOWN").upper()
        raw_date = str(envelope.get("report_date") or date.today().isoformat())
        try:
            report_date = date.fromisoformat(raw_date)
        except ValueError:
            report_date = date.today()
        usage_value = envelope.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        request_timestamp = _parse_timestamp(envelope.get("request_timestamp"))
        generated = GeneratedDeepResearchReport(
            report=repaired.report,
            model=str(envelope.get("model") or "unknown"),
            usage=TokenUsage(
                input_tokens=_optional_int(usage.get("input_tokens")),
                output_tokens=_optional_int(usage.get("output_tokens")),
                total_tokens=_optional_int(usage.get("total_tokens")),
                cached_input_tokens=_optional_int(usage.get("cached_input_tokens")),
            ),
            response_id=(str(envelope["response_id"]) if envelope.get("response_id") else None),
            request_timestamp=request_timestamp,
            response_status=(
                str(envelope["response_status"]) if envelope.get("response_status") else None
            ),
            incomplete_details=envelope.get("incomplete_details"),
        )
        research = PreparedResearch(
            ticker=ticker,
            report_date=report_date,
            evidence=evidence,
            evidence_json=json.dumps(evidence, ensure_ascii=False),
            allowed_references=allowed_references,
            news_count=sum(
                1
                for item in evidence.get("ranked_information_items", [])
                if isinstance(item, dict) and str(item.get("reference", "")).startswith("[News ")
            ),
            filing_count=sum(
                1
                for item in evidence.get("ranked_information_items", [])
                if isinstance(item, dict) and str(item.get("reference", "")).startswith("[Filing ")
            ),
        )
        prepared = PreparedDeepResearch(
            research=research,
            request_json="",
            allowed_main_clusters=allowed_clusters,
            evidence_depth_by_reference=depth_by_reference,
            profile_configured=bool(profile_fields),
            profile_field_count=len(profile_fields),
            profile_configured_fields=profile_fields,
        )
        markdown = render_deep_report(prepared, generated)
        markdown_path = markdown_path or path.with_suffix(".md")
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown, encoding="utf-8")
        return CompletedDeepResearch(
            generated=generated,
            markdown=markdown,
            path=markdown_path,
            structured_path=path,
            validation=validation,
        )

    def recover_rejected_response(self, path: Path) -> CompletedDeepResearch:
        """Recover a completed schema-valid response entirely offline."""

        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        validation_path = path.with_name(f"{path.stem}-validation.json")
        validation_hash = (
            hashlib.sha256(validation_path.read_bytes()).hexdigest()
            if validation_path.exists()
            else None
        )
        envelope = load_response_envelope(path)
        if envelope.get("artifact_status") != "rejected":
            raise SavedReportValidationError(
                "Recovery requires a rejected structured-response artifact.",
                ValidationSummary(
                    status=ValidationStatus.REJECTED,
                    issues=[
                        ValidationIssue(
                            code="not_rejected_artifact",
                            severity=ValidationSeverity.FATAL,
                            message="The supplied artifact is not marked as rejected.",
                        )
                    ],
                ),
            )
        ticker = str(envelope.get("ticker") or "UNKNOWN").upper()
        report_date = str(envelope.get("report_date") or date.today().isoformat())
        markdown_path = self._reports_root / report_date / f"{ticker}-deep-recovered.md"
        completed = self.validate_saved_response(path, markdown_path=markdown_path)
        recovered_json = self._artifacts.save_recovered(
            source_path=path,
            source_envelope=envelope,
            repaired_report=completed.generated.report,
            validation=completed.validation,
            recovery_timestamp=datetime.now(timezone.utc),
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError("Original rejected artifact changed during recovery.")
        if validation_hash is not None and (
            not validation_path.exists()
            or hashlib.sha256(validation_path.read_bytes()).hexdigest() != validation_hash
        ):
            raise RuntimeError("Original validation summary changed during recovery.")
        return replace(completed, structured_path=recovered_json)


def render_deep_report(
    prepared: PreparedDeepResearch,
    generated: GeneratedDeepResearchReport,
) -> str:
    return render_compact_deep_report(
        ticker=prepared.research.ticker,
        report_date=prepared.research.report_date.isoformat(),
        generated=generated,
        usage_text=_usage_text(generated),
    )


def _render_legacy_deep_report(
    prepared: PreparedDeepResearch,
    generated: GeneratedDeepResearchReport,
) -> str:
    report = generated.report
    research = prepared.research
    lines = [
        f"# Deep investment research report: {research.ticker}",
        "",
        f"- Report date: {research.report_date.isoformat()}",
        f"- Model: {generated.model}",
        f"- Token usage: {_usage_text(generated)}",
        "- Evidence-grounded research support; no trade is executed.",
        "",
        "## 1. Bottom line",
        "",
        f"**Current view: {report.bottom_line.current_view.value}.**",
        "",
    ]
    lines.extend(_point(point) for point in report.bottom_line.sentences)
    movement = report.stock_movement
    lines.extend(
        [
            "",
            "## 2. What happened to the stock?",
            "",
            f"- Today: {_point(movement.today)}",
            f"- Five days: {_point(movement.five_day)}",
            f"- Thirty days: {_point(movement.thirty_day)}",
            f"- Ninety days: {_point(movement.ninety_day)}",
            f"- Pattern: **{movement.pattern.value}** — {_point(movement.pattern_reason)}",
            f"- Volume: {_point(movement.volume_interpretation)}",
            "",
            "**Possible explanations, ranked:**",
            "",
        ]
    )
    lines.extend(
        f"- **{item.rank.value}:** {_point(item.explanation)}"
        for item in movement.possible_explanations
    )
    lines.extend(["", "## 3. The 3 most important developments", ""])
    if report.main_developments:
        for development in report.main_developments:
            _add_development(lines, development)
    else:
        lines.extend(
            [
                "No event met the evidence threshold. Weak articles were not used to fill space.",
                "",
            ]
        )
    lines.extend(["## 4. What matters most right now?", ""])
    lines.append(f"**{report.biggest_issue.issue}**")
    lines.append("")
    lines.extend(_point(point) for point in report.biggest_issue.explanation)
    lines.extend(
        [
            "",
            f"Connection to the stock: {_point(report.biggest_issue.connection_to_stock)}",
            "",
            "## 5. My current view",
            "",
            f"**{report.current_view.view.value}**",
            "",
            f"- Strongest reason: {_point(report.current_view.strongest_reason)}",
            f"- Strongest counterargument: {_point(report.current_view.strongest_counterargument)}",
            "- What would change the view: "
            f"{_point(report.current_view.evidence_that_would_change_view)}",
            "",
            "## 6. What may happen next?",
            "",
            "_All probability ranges are subjective analytical estimates, not guarantees._",
            "",
        ]
    )
    _add_horizon(lines, report.next_one_to_two_weeks)
    _add_horizon(lines, report.next_one_to_three_months)
    _add_horizon(lines, report.long_term)
    lines.extend(
        [
            "## 7. What should the investor do?",
            "",
            f"Profile basis: {report.investor_guidance.profile_basis}",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report.investor_guidance.guidance)
    lines.extend(
        [
            "",
            f"**Specific next step:** {report.investor_guidance.specific_next_step}",
            "",
            "## 8. What would strengthen or weaken the case?",
            "",
            "**Would strengthen the case:**",
            "",
        ]
    )
    _add_points(lines, report.thesis_checks.strengthening_evidence)
    lines.extend(["", "**Would weaken the case:**", ""])
    _add_points(lines, report.thesis_checks.weakening_evidence)
    lines.extend(["", "**Upcoming catalysts:**", ""])
    _add_points(lines, report.thesis_checks.upcoming_catalysts)
    lines.extend(["", "**Thesis invalidation conditions:**", ""])
    _add_points(lines, report.thesis_checks.invalidation_conditions)
    lines.extend(["", "## 9. What is still missing?", ""])
    if report.missing_information:
        lines.extend(f"- {item}" for item in report.missing_information)
    else:
        lines.append("- No material evidence gap was identified in the supplied dataset.")
    confidence = report.confidence_measures
    lines.extend(
        [
            "",
            "## 10. Confidence in the evidence",
            "",
            (
                "These levels describe evidence quality, not confidence in predicting "
                "the future share price."
            ),
            "",
        ]
    )
    for label, measure in (
        ("Market data", confidence.market_data_confidence),
        ("News content", confidence.news_content_confidence),
        ("Catalyst identification", confidence.catalyst_confidence),
        ("Fundamental data", confidence.fundamental_data_confidence),
        ("Short-term forecast", confidence.short_term_forecast_confidence),
        ("Long-term thesis", confidence.long_term_thesis_confidence),
    ):
        lines.append(
            f"- **{label}: {measure.level.value}.** {measure.explanation} "
            f"{' '.join(measure.references)}"
        )
    return "\n".join(lines).rstrip() + "\n"


def _add_development(lines: list[str], development: object) -> None:
    lines.extend(
        [
            f"### {development.title}",
            "",
            f"**What happened:** {_point(development.what_happened)}",
            "",
            "**Key facts:**",
            "",
        ]
    )
    _add_points(lines, development.key_facts)
    lines.extend(["", "**Why it matters:**", ""])
    _add_points(lines, development.why_it_matters)
    lines.extend(
        [
            "",
            f"- Assessment: **{development.direction.value}**",
            f"- Likely impact: **{development.likely_horizon.value}**",
            f"- Reliability: **{development.reliability.value}** — "
            f"{development.reliability_reason}",
            f"- Evidence type: **{development.evidence_type.value}**",
            f"- Watch next: {_point(development.what_to_watch_next)}",
            "",
        ]
    )


def _add_horizon(lines: list[str], horizon: object) -> None:
    lines.extend([f"### {horizon.horizon}", ""])
    for label, scenario in (
        ("Most likely", horizon.most_likely),
        ("Positive", horizon.positive),
        ("Negative", horizon.negative),
    ):
        probability = f"{scenario.probability_low_percent}–{scenario.probability_high_percent}%"
        lines.extend(
            [
                f"**{label} scenario ({probability} subjective probability):**",
                "",
                f"- What could happen: {_point(scenario.what_could_happen)}",
                f"- Why and key drivers: {_point(scenario.why_and_key_drivers)}",
                f"- Evidence to watch: {_point(scenario.supporting_evidence_to_watch)}",
                f"- Would invalidate it: {_point(scenario.invalidation_condition)}",
                "",
            ]
        )


def _add_points(lines: list[str], points: list[object]) -> None:
    lines.extend(f"- {_point(point)}" for point in points)


def _point(point: object) -> str:
    return f"{point.text} {' '.join(point.references)}"


def _usage_text(generated: GeneratedDeepResearchReport) -> str:
    usage = generated.usage
    if all(
        value is None for value in (usage.input_tokens, usage.output_tokens, usage.total_tokens)
    ):
        return "not provided by API"
    return (
        f"input={usage.input_tokens if usage.input_tokens is not None else 'unknown'}, "
        f"cached_input="
        f"{usage.cached_input_tokens if usage.cached_input_tokens is not None else 'unknown'}, "
        f"output={usage.output_tokens if usage.output_tokens is not None else 'unknown'}, "
        f"total={usage.total_tokens if usage.total_tokens is not None else 'unknown'}"
    )


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
