"""Deterministic quality checks for compact deep reports."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from pydantic import BaseModel

from investment_assistant.domain.deep_report import (
    DeepResearchReport,
    ImpactDirection,
    KeyEvidence,
    ThesisStatus,
    TimeHorizon,
)
from investment_assistant.domain.report_validation import (
    ValidationIssue,
    ValidationSeverity,
)

ACTION_WORDS = {
    "add",
    "check",
    "hold",
    "monitor",
    "position",
    "reconsider",
    "research",
    "review",
    "verify",
    "wait",
}
FORMAL_PHRASES = {
    "aforementioned",
    "herein",
    "heretofore",
    "notwithstanding the foregoing",
    "pursuant to",
}
JARGON_TERMS = {
    "dilution",
    "free cash flow",
    "lock-up",
    "operating margin",
    "price-to-sales",
}
UNHELPFUL_PHRASES = {
    "further research is required",
    "mixed implications",
    "monitor developments",
}
UNSUPPORTED_READING_PHRASES = {
    "after reading the article",
    "complete filing",
    "entire article",
    "entire filing",
    "full article says",
    "full filing says",
}
UNCERTAINTY_PHRASES = {
    "cannot be concluded",
    "insufficient evidence",
    "not enough evidence",
    "unclear from the supplied evidence",
}
PROFILE_KEYWORDS = {
    "already_owns_stock": {"already hold", "already own", "current position"},
    "average_purchase_price": {"average purchase", "purchase price", "cost basis"},
    "amount_invested": {"amount invested", "starter position", "small position"},
    "current_position_value": {"position value", "current position"},
    "intended_additional_investment": {"add", "additional investment"},
    "intended_holding_period": {"holding period", "long term", "years"},
    "risk_tolerance": {"risk tolerance", "risk level"},
    "maximum_acceptable_loss": {"acceptable loss", "loss limit"},
    "maximum_portfolio_percentage": {"portfolio", "position size", "concentration"},
    "investment_objective": {"capital growth", "growth", "objective"},
    "reason_for_investing": {"original thesis", "reason for investing", "thesis"},
    "position_description": {"starter position", "small position", "position size"},
    "trading_preference": {"frequent trading", "short-term trading", "long term"},
}
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?%?")
NUMBER_WITH_UNIT_PATTERN = re.compile(
    r"(?<![A-Za-z])(?P<number>[-+]?\d[\d,]*(?:\.\d+)?)"
    r"(?P<percent>%|\s+(?P<scale>million|billion|trillion))?",
    re.IGNORECASE,
)
WORD_PATTERN = re.compile(r"[a-z0-9]+")
DISPLAY_QUANTUM = Decimal("0.01")
SCALE_VALUES = {
    "million": Decimal("1000000"),
    "billion": Decimal("1000000000"),
    "trillion": Decimal("1000000000000"),
}


@dataclass(frozen=True, slots=True)
class NumericCandidate:
    value: Decimal
    kind: str


def validate_deep_report_quality(
    report: DeepResearchReport,
    *,
    evidence: dict[str, object],
    configured_profile_fields: tuple[str, ...],
) -> None:
    """Reject only factual or evidence-integrity failures."""

    del configured_profile_fields
    report_text = " ".join(_all_strings(report))
    _validate_evidence_depth_language(report_text)
    _validate_asset_specific_framework(report, evidence)
    _validate_fundamental_evidence_use(report, evidence)
    _validate_numeric_facts(report.key_evidence, evidence)
    _validate_no_long_extract_copy(report_text, evidence)


def _validate_fundamental_evidence_use(
    report: DeepResearchReport, evidence: dict[str, object]
) -> None:
    context = evidence.get("fundamental_and_valuation_context")
    if not isinstance(context, dict):
        return
    if context.get("applicability") == "not_applicable":
        return
    excluded = context.get("excluded_non_decision_grade_metrics", [])
    excluded_names = {
        str(item.get("name", "")).lower()
        for item in excluded
        if isinstance(item, dict) and item.get("name")
    }
    for item in report.key_evidence:
        fact = item.fact.lower()
        if (
            item.reference == "[Market Data]"
            and any(name in fact for name in excluded_names)
            and NUMBER_PATTERN.search(fact)
        ):
            raise ValueError(
                "Report treats an excluded third-party fundamental or valuation figure "
                "as decision-grade evidence."
            )

    decision_grade = context.get("decision_grade_metrics", [])
    if report.long_term_thesis.status is not ThesisStatus.WEAKENED or decision_grade:
        return
    independent_negative_support = any(
        item.reference != "[Market Data]"
        and item.time_horizon is TimeHorizon.LONG_TERM
        and item.impact_direction in {ImpactDirection.NEGATIVE, ImpactDirection.MIXED}
        for item in report.key_evidence
    )
    if not independent_negative_support:
        raise ValueError(
            "Long-term thesis cannot be weakened mainly from non-decision-grade "
            "third-party fundamentals."
        )


def _validate_asset_specific_framework(
    report: DeepResearchReport, evidence: dict[str, object]
) -> None:
    scope = evidence.get("scope")
    if not isinstance(scope, dict) or scope.get("asset_type") != "commodity_etp":
        return
    gap_terms = (
        "revenue",
        "net income",
        "operating margin",
        "p/e",
        "free cash flow",
        "balance-sheet valuation",
    )
    gap_words = ("missing", "unavailable", "not available", "evidence gap", "lack of")
    for limitation in report.limitations:
        lowered = limitation.lower()
        if any(term in lowered for term in gap_terms) and any(
            marker in lowered for marker in gap_words
        ):
            raise ValueError(
                "Company-equity metrics must not be described as evidence gaps for a "
                "commodity ETP."
            )

    thesis_text = " ".join(_all_strings(report.long_term_thesis)).lower()
    if not any(term in thesis_text for term in ("futures curve", "contango", "backwardation", "roll")):
        raise ValueError("Commodity ETP long-term thesis does not consider the futures curve.")

    report_text = " ".join(_all_strings(report)).lower()
    low_volume_claim = re.search(
        r"low (?:exchange[- ]trading |exchange |trading )?volume.{0,80}"
        r"(?:illiquid|poor liquidity)",
        report_text,
    )
    if low_volume_claim and "volume alone" not in low_volume_claim.group(0):
        raise ValueError(
            "Low exchange volume alone cannot establish poor commodity ETP liquidity."
        )


def collect_deep_report_warnings(
    report: DeepResearchReport,
    *,
    evidence: dict[str, object],
    configured_profile_fields: tuple[str, ...],
) -> list[ValidationIssue]:
    """Return non-fatal decision-usefulness warnings."""

    report_text = " ".join(_all_strings(report))
    warnings: list[ValidationIssue] = []
    checks = (
        ("investor_guidance", lambda: _validate_guidance(report, configured_profile_fields)),
        ("beginner_language", lambda: _validate_beginner_language(report_text)),
        ("repetitive_uncertainty", lambda: _validate_uncertainty(report_text)),
        ("headline_restatement", lambda: _validate_headline_explanation(report, evidence)),
    )
    for code, check in checks:
        try:
            check()
        except ValueError as error:
            warnings.append(
                ValidationIssue(
                    code=code,
                    severity=ValidationSeverity.WARNING,
                    message=str(error),
                )
            )
    approximate_tokens = max(1, len(report.model_dump_json()) // 4)
    if approximate_tokens > 3_500:
        warnings.append(
            ValidationIssue(
                code="report_length",
                severity=ValidationSeverity.WARNING,
                message=(
                    f"The structured report is approximately {approximate_tokens:,} tokens, "
                    "above the preferred 3,500-token target."
                ),
            )
        )
    if any("unavailable" in item.lower() for item in report.limitations):
        warnings.append(
            ValidationIssue(
                code="optional_data_unavailable",
                severity=ValidationSeverity.WARNING,
                message="Some optional data is unavailable in the supplied evidence.",
            )
        )
    return warnings


def _validate_guidance(
    report: DeepResearchReport,
    configured_profile_fields: tuple[str, ...],
) -> None:
    action = report.investor_action
    combined = " ".join(
        [
            action.action_for_current_position.value,
            action.whether_to_add_more_now.value,
            *action.evidence_before_adding,
            action.reason,
        ]
    ).lower()
    words = set(WORD_PATTERN.findall(combined))
    if not words.intersection(ACTION_WORDS):
        raise ValueError("Investor action lacks a concrete next step.")
    for phrase in UNHELPFUL_PHRASES:
        position = combined.find(phrase)
        if position >= 0 and len(WORD_PATTERN.findall(combined[position + len(phrase) :])) < 4:
            raise ValueError(f"Investor action uses an unexplained generic phrase: {phrase}.")
    if configured_profile_fields:
        relevant = {
            keyword
            for field in configured_profile_fields
            for keyword in PROFILE_KEYWORDS.get(field, set())
        }
        if relevant and not any(keyword in combined for keyword in relevant):
            raise ValueError("Investor action ignored the configured investor profile.")


def _validate_beginner_language(report_text: str) -> None:
    normalized = report_text.lower()
    for phrase in FORMAL_PHRASES:
        if phrase in normalized:
            raise ValueError(f"Report uses unnecessarily formal language: {phrase}.")
    for sentence in re.split(r"(?<=[.!?])\s+", report_text):
        lowered = sentence.lower()
        for term in JARGON_TERMS:
            if term in lowered and not any(
                marker in lowered
                for marker in (" means ", " meaning ", " that is ", "(", "in other words")
            ):
                raise ValueError(
                    f"Report uses unexplained beginner-level financial jargon: {term}."
                )


def _validate_uncertainty(report_text: str) -> None:
    normalized = report_text.lower()
    occurrences = sum(normalized.count(phrase) for phrase in UNCERTAINTY_PHRASES)
    if occurrences > 3:
        raise ValueError("Report repeats insufficient-evidence language too often.")


def _validate_evidence_depth_language(report_text: str) -> None:
    normalized = report_text.lower()
    for phrase in UNSUPPORTED_READING_PHRASES:
        positions = [match.start() for match in re.finditer(re.escape(phrase), normalized)]
        if any(not _scope_phrase_is_negated(normalized, position) for position in positions):
            raise ValueError(f"Report implies unsupported full-text access: {phrase}.")


def _scope_phrase_is_negated(text: str, position: int) -> bool:
    context = text[max(0, position - 40) : position]
    return bool(re.search(r"\b(?:no|not|without|unavailable)\b[^.!?;]{0,30}$", context))


def _validate_headline_explanation(
    report: DeepResearchReport,
    evidence: dict[str, object],
) -> None:
    headlines = _headline_by_reference(evidence)
    for item in report.key_evidence:
        headline = headlines.get(item.reference)
        if not headline:
            continue
        headline_tokens = set(WORD_PATTERN.findall(headline.lower()))
        fact_tokens = set(WORD_PATTERN.findall(item.fact.lower()))
        union = headline_tokens | fact_tokens
        similarity = len(headline_tokens & fact_tokens) / len(union) if union else 0
        if similarity >= 0.82 and len(fact_tokens) <= len(headline_tokens) + 3:
            raise ValueError(f"Evidence {item.reference} merely restates its headline.")


def _validate_numeric_facts(
    points: list[KeyEvidence],
    evidence: dict[str, object],
) -> None:
    evidence_by_reference = _evidence_by_reference(evidence)
    for point in points:
        if point.reference == "[Market Data]":
            _validate_market_numeric_fact(point.fact, evidence)
            continue
        claimed = {_normalize_number(value) for value in NUMBER_PATTERN.findall(point.fact)}
        if not claimed:
            continue
        cited_text = evidence_by_reference.get(point.reference, "")
        supported = {_normalize_number(value) for value in NUMBER_PATTERN.findall(cited_text)}
        supported_values = [
            decimal
            for value in NUMBER_PATTERN.findall(cited_text)
            if (decimal := _to_decimal(value.rstrip("%"))) is not None
        ]
        unsupported = {
            value
            for value in claimed - supported
            if not _matches_canonical_decimal(value, supported_values)
        }
        if unsupported:
            raise ValueError(
                "A numerical fact is absent from its cited evidence: "
                f"{', '.join(sorted(unsupported))}."
            )


def _matches_canonical_decimal(
    claimed_text: str,
    supported: list[Decimal],
) -> bool:
    claimed = _to_decimal(claimed_text)
    if claimed is None:
        return False
    return any(
        claimed == _round_display(value) or abs(claimed - value) <= DISPLAY_QUANTUM
        for value in supported
    )


def _validate_market_numeric_fact(fact: str, evidence: dict[str, object]) -> None:
    """Accept canonical human display values while rejecting material mismatches."""

    evidence_text = _evidence_by_reference(evidence)["[Market Data]"]
    exact_supported = {_normalize_number(value) for value in NUMBER_PATTERN.findall(evidence_text)}
    candidates = _market_numeric_candidates(evidence)
    unsupported: list[str] = []
    for match in NUMBER_WITH_UNIT_PATTERN.finditer(fact):
        if _is_period_label(fact, match.start(), match.end()):
            continue
        raw = match.group("number")
        normalized = _normalize_number(raw + (match.group("percent") or ""))
        if normalized in exact_supported:
            continue
        try:
            claimed = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        claimed = _apply_direction_context(claimed, fact, match.start())
        scale = (match.group("scale") or "").lower()
        is_percent = match.group("percent") == "%"
        if _matches_numeric_candidate(claimed, scale, is_percent, candidates):
            continue
        unsupported.append(raw + (f" {scale}" if scale else "%" if is_percent else ""))
    if unsupported:
        raise ValueError(
            "A numerical fact is absent from its cited evidence: "
            f"{', '.join(dict.fromkeys(unsupported))}."
        )


def _matches_numeric_candidate(
    claimed: Decimal,
    scale: str,
    is_percent: bool,
    candidates: list[NumericCandidate],
) -> bool:
    if scale:
        multiplier = SCALE_VALUES[scale]
        displayed_tolerance = _display_unit_tolerance(claimed)
        for candidate in candidates:
            if candidate.kind not in {"currency", "shares", "volume", "generic"}:
                continue
            displayed = candidate.value / multiplier
            if (
                claimed == _round_display(displayed)
                or abs(claimed - displayed) <= displayed_tolerance
            ):
                return True
        return False
    if is_percent:
        return any(
            candidate.kind == "percentage"
            and (
                claimed == _round_display(candidate.value)
                or abs(claimed - candidate.value) <= DISPLAY_QUANTUM
            )
            for candidate in candidates
        )
    for candidate in candidates:
        if candidate.kind in {"price", "percentage", "multiple"} and (
            claimed == _round_display(candidate.value)
            or abs(claimed - candidate.value) <= DISPLAY_QUANTUM
        ):
            return True
        if candidate.kind in {"volume", "shares"} and claimed == candidate.value:
            return True
    return False


def _market_numeric_candidates(evidence: dict[str, object]) -> list[NumericCandidate]:
    candidates: list[NumericCandidate] = []
    sections = [
        evidence.get("market_data"),
        evidence.get("market_context"),
        evidence.get("fundamental_and_valuation_context"),
        evidence.get("commodity_etp_context"),
    ]

    def walk(value: object, key: str = "", unit: str = "") -> None:
        if isinstance(value, dict):
            next_unit = str(value.get("unit", unit)).lower()
            for child_key, child in value.items():
                walk(child, str(child_key), next_unit)
            return
        if isinstance(value, list):
            for child in value:
                walk(child, key, unit)
            return
        decimal = _to_decimal(value)
        if decimal is None:
            return
        lowered = key.lower()
        if "percent" in lowered or "percentage" in lowered:
            kind = "percentage"
        elif "volume" in lowered:
            kind = "volume"
        elif lowered in {"latest_close", "previous_close"} or "price" in lowered:
            kind = "price"
        elif unit == "ratio":
            candidates.append(NumericCandidate(decimal, "generic"))
            candidates.append(NumericCandidate(decimal * Decimal("100"), "percentage"))
            return
        elif unit == "percent":
            kind = "percentage"
        elif unit == "currency":
            kind = "currency"
        elif unit == "shares":
            kind = "shares"
        elif unit == "multiple":
            kind = "multiple"
        else:
            kind = "generic"
        candidates.append(NumericCandidate(decimal, kind))

    for section in sections:
        walk(section)
    return candidates


def _to_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace(",", "")
    match = re.fullmatch(
        r"([-+]?\d+(?:\.\d+)?)\s*(million|billion|trillion)?%?",
        cleaned,
        re.IGNORECASE,
    )
    if not match:
        return None
    try:
        result = Decimal(match.group(1))
    except InvalidOperation:
        return None
    scale = (match.group(2) or "").lower()
    return result * SCALE_VALUES.get(scale, Decimal("1"))


def _round_display(value: Decimal) -> Decimal:
    return value.quantize(DISPLAY_QUANTUM, rounding=ROUND_HALF_UP)


def _display_unit_tolerance(value: Decimal) -> Decimal:
    decimal_places = max(0, -value.as_tuple().exponent)
    return Decimal("0.5") * (Decimal("10") ** -decimal_places)


def _apply_direction_context(value: Decimal, text: str, position: int) -> Decimal:
    if value < 0:
        return value
    context = text[max(0, position - 80) : position].lower()
    following = text[position : position + 45].lower()
    if re.match(r"[-+\d,.%\s]{1,20}\b(?:below|under|lower)\b", following):
        return -value
    if re.match(r"[-+\d,.%\s]{1,20}\b(?:above|higher)\b", following):
        return value
    negative = max(
        (
            context.rfind(word)
            for word in (
                "down",
                "fell",
                "fallen",
                "declined",
                "dropped",
                "below",
                "under",
                "lower",
            )
        ),
        default=-1,
    )
    positive = max(
        (
            context.rfind(word)
            for word in ("up", "rose", "risen", "gained", "increased", "above", "higher")
        ),
        default=-1,
    )
    return -value if negative > positive else value


def _is_period_label(text: str, start: int, end: int) -> bool:
    after = text[end : end + 12].lower()
    before = text[max(0, start - 8) : start].lower()
    return bool(
        re.match(r"\s*-?\s*(?:trading\s+)?(?:day|days|week|weeks|month|months)\b", after)
        or re.search(r"\b(?:day|week|month)[- ]$", before)
    )


def _validate_no_long_extract_copy(report_text: str, evidence: dict[str, object]) -> None:
    report_words = WORD_PATTERN.findall(report_text.lower())
    report_sequences = {
        tuple(report_words[index : index + 18]) for index in range(max(0, len(report_words) - 17))
    }
    for extract in _content_extracts(evidence):
        words = WORD_PATTERN.findall(extract.lower())
        for index in range(max(0, len(words) - 17)):
            if tuple(words[index : index + 18]) in report_sequences:
                raise ValueError("Report reproduces a long passage from supplied source content.")


def _headline_by_reference(evidence: dict[str, object]) -> dict[str, str]:
    items = evidence.get("ranked_information_items", [])
    if not isinstance(items, list):
        return {}
    return {
        str(item["reference"]): str(item["headline"])
        for item in items
        if isinstance(item, dict) and item.get("reference") and item.get("headline")
    }


def _evidence_by_reference(evidence: dict[str, object]) -> dict[str, str]:
    result = {
        "[Market Data]": json.dumps(
            {
                "market_data": evidence.get("market_data"),
                "market_context": evidence.get("market_context"),
                "fundamentals": evidence.get("fundamental_and_valuation_context"),
                "commodity_etp_context": evidence.get("commodity_etp_context"),
            },
            ensure_ascii=False,
            default=str,
        )
    }
    items = evidence.get("ranked_information_items", [])
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("reference"):
                serialized = json.dumps(item, ensure_ascii=False, default=str)
                derived = _filing_derived_totals(item)
                result[str(item["reference"])] = serialized + " " + " ".join(derived)
    return result


def _content_extracts(evidence: dict[str, object]) -> Iterable[str]:
    items = evidence.get("ranked_information_items", [])
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("content_extract", "summary_supplied_by_source"):
            value = item.get(key)
            if isinstance(value, str):
                yield value
        filing = item.get("filing_metadata")
        if isinstance(filing, dict):
            sections = filing.get("extracted_sections", [])
            if isinstance(sections, list):
                for section in sections:
                    if isinstance(section, dict) and isinstance(section.get("text"), str):
                        yield section["text"]


def _normalize_number(value: str) -> str:
    cleaned = value.replace(",", "").removeprefix("+").rstrip("%")
    try:
        decimal = Decimal(cleaned)
    except InvalidOperation:
        return cleaned
    return format(decimal.normalize(), "f")


def _filing_derived_totals(item: dict[str, object]) -> list[str]:
    filing = item.get("filing_metadata")
    if not isinstance(filing, dict):
        return []
    summary = filing.get("material_event_summary")
    if not isinstance(summary, str):
        return []
    amounts: dict[str, list[Decimal]] = {}
    pattern = re.compile(
        r"\$?([-+]?\d[\d,]*(?:\.\d+)?)\s+(million|billion|trillion)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(summary):
        try:
            numeric = Decimal(match.group(1).replace(",", ""))
        except InvalidOperation:
            continue
        amounts.setdefault(match.group(2).lower(), []).append(numeric)
    return [
        f"{format(sum(values).normalize(), 'f')} {scale}"
        for scale, values in amounts.items()
        if len(values) >= 2
    ]


def _all_strings(value: BaseModel) -> Iterable[str]:
    def walk(item: object) -> Iterable[str]:
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for child in item.values():
                yield from walk(child)
        elif isinstance(item, list):
            for child in item:
                yield from walk(child)

    yield from walk(value.model_dump(mode="python"))
