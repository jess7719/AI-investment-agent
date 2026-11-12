"""Deterministic local formatting repairs that never call a model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from investment_assistant.domain.deep_report import DeepResearchReport
from investment_assistant.domain.report_validation import (
    ValidationIssue,
    ValidationSeverity,
)

TARGET_SENTENCE_WORDS = 45
REVIEW_SENTENCE_WORDS = 70
DECIMAL_PATTERN = re.compile(r"(?<![A-Za-z0-9])[-+]?\d[\d,]*\.\d{3,}%?")
VOLUME_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9])")
MARKDOWN_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+)")
PROTECTED_PATTERN = re.compile(
    r"https?://\S+|\[(?:Market Data|News \d+|Filing \d+)\]|"
    r"(?:[A-Za-z]\.){2,}|\d+\.\d+"
)
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
DISCLAIMER_PHRASES = (
    "this is not financial advice",
    "past performance does not guarantee future results",
    "consult a regulated financial adviser",
)
NEGATED_SCOPE_PATTERNS = (
    (
        re.compile(
            r";\s*no complete article body or complete filing (?:was|were) "
            r"(?:available|supplied|provided)",
            re.IGNORECASE,
        ),
        "",
    ),
    (
        re.compile(
            r"no complete article body or complete filing (?:was|were) "
            r"(?:available|supplied|provided)",
            re.IGNORECASE,
        ),
        "only bounded source extracts and metadata were supplied",
    ),
    (
        re.compile(
            r"no (?:complete|full|entire) filing (?:was|were) "
            r"(?:available|supplied|provided)",
            re.IGNORECASE,
        ),
        "only bounded filing evidence was supplied",
    ),
)
BOUNDED_FILING_SCOPE_PATTERNS = (
    (
        re.compile(
            r"\bthe (?:complete|full|entire) filing (?:shows|states|confirms|indicates)\b",
            re.IGNORECASE,
        ),
        "the supplied filing extract indicates",
    ),
    (
        re.compile(r"\bthe filing confirms\b", re.IGNORECASE),
        "the bounded filing evidence indicates",
    ),
    (
        re.compile(r"\bthe (?:complete|full|entire) filing says\b", re.IGNORECASE),
        "the supplied filing extract states",
    ),
)


@dataclass(frozen=True, slots=True)
class RepairedDeepReport:
    report: DeepResearchReport
    issues: tuple[ValidationIssue, ...]


def repair_deep_report(
    report: DeepResearchReport,
    *,
    evidence: dict[str, object] | None = None,
) -> RepairedDeepReport:
    """Repair user-facing formatting while preserving the original report separately."""

    issues: list[ValidationIssue] = []
    repaired = _walk(
        report.model_dump(mode="python"),
        "",
        issues,
        bounded_filing_evidence=_has_bounded_filing_evidence(evidence),
    )
    return RepairedDeepReport(
        DeepResearchReport.model_validate(repaired),
        tuple(issues),
    )


def _walk(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
    *,
    bounded_filing_evidence: bool,
) -> Any:
    if isinstance(value, dict):
        return {
            key: _walk(
                child,
                f"{path}.{key}".lstrip("."),
                issues,
                bounded_filing_evidence=bounded_filing_evidence,
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        repaired = [
            _walk(
                child,
                f"{path}[{index}]",
                issues,
                bounded_filing_evidence=bounded_filing_evidence,
            )
            for index, child in enumerate(value)
        ]
        if repaired and all(isinstance(child, str) for child in repaired):
            unique = list(dict.fromkeys(repaired))
            if len(unique) != len(repaired) and unique:
                issues.append(
                    ValidationIssue(
                        code="duplicate_list_item",
                        severity=ValidationSeverity.REPAIRABLE,
                        message="Duplicate bullet text was removed locally.",
                        location=path,
                    )
                )
                return unique
        return repaired
    if isinstance(value, str):
        return _repair_text(
            value,
            path,
            issues,
            bounded_filing_evidence=bounded_filing_evidence,
        )
    return value


def _repair_text(
    value: str,
    path: str,
    issues: list[ValidationIssue],
    *,
    bounded_filing_evidence: bool,
) -> str:
    repaired = value
    without_prefix = MARKDOWN_PREFIX.sub("", repaired)
    if without_prefix != repaired:
        repaired = without_prefix
        issues.append(
            ValidationIssue(
                code="markdown_prefix",
                severity=ValidationSeverity.REPAIRABLE,
                message="Embedded Markdown heading or bullet formatting was removed.",
                location=path,
            )
        )
    repaired = _remove_repeated_disclaimer(repaired, path, issues)
    repaired = _repair_evidence_scope_language(
        repaired,
        path,
        issues,
        bounded_filing_evidence=bounded_filing_evidence,
    )
    is_volume = "volume" in path.lower() or "volume" in repaired.lower()
    number_pattern = VOLUME_NUMBER_PATTERN if is_volume else DECIMAL_PATTERN
    formatted = number_pattern.sub(
        lambda match: _format_number(match.group(0), f"{path}.volume" if is_volume else path),
        repaired,
    )
    if formatted != repaired:
        repaired = formatted
        issues.append(
            ValidationIssue(
                code="decimal_precision",
                severity=ValidationSeverity.REPAIRABLE,
                message="Excessive decimal precision was formatted locally.",
                location=path,
            )
        )
    split_text, split_issues = _repair_long_sentences(repaired, path)
    issues.extend(split_issues)
    return split_text


def _repair_evidence_scope_language(
    text: str,
    path: str,
    issues: list[ValidationIssue],
    *,
    bounded_filing_evidence: bool,
) -> str:
    repaired = text
    patterns = list(NEGATED_SCOPE_PATTERNS)
    if bounded_filing_evidence:
        patterns.extend(BOUNDED_FILING_SCOPE_PATTERNS)
    for pattern, replacement in patterns:
        repaired, count = pattern.subn(replacement, repaired)
        if count:
            issues.append(
                ValidationIssue(
                    code="evidence_scope_wording",
                    severity=ValidationSeverity.REPAIRABLE,
                    message=(
                        "Evidence-scope wording was narrowed locally to match the "
                        "bounded material supplied."
                    ),
                    location=path,
                )
            )
    return repaired


def _has_bounded_filing_evidence(evidence: dict[str, object] | None) -> bool:
    if not isinstance(evidence, dict):
        return False
    items = evidence.get("ranked_information_items", [])
    if not isinstance(items, list):
        return False
    return any(
        isinstance(item, dict)
        and str(item.get("reference", "")).startswith("[Filing ")
        and str(item.get("evidence_depth", ""))
        in {"official filing extract", "public metadata description", "headline only"}
        for item in items
    )


def _remove_repeated_disclaimer(
    text: str,
    path: str,
    issues: list[ValidationIssue],
) -> str:
    lowered = text.lower()
    for phrase in DISCLAIMER_PHRASES:
        if phrase not in lowered:
            continue
        start = lowered.index(phrase)
        end = start + len(phrase)
        remaining = (text[:start] + text[end:]).strip(" .;:-")
        issues.append(
            ValidationIssue(
                code="repeated_disclaimer",
                severity=ValidationSeverity.REPAIRABLE,
                message="A repeated disclaimer was removed; the report-level notice remains.",
                location=path,
            )
        )
        return remaining or "See the report-level research-use notice."
    return text


def _format_number(raw: str, path: str) -> str:
    suffix = "%" if raw.endswith("%") else ""
    numeric = raw.rstrip("%").replace(",", "")
    value = float(numeric)
    if "volume" in path.lower() and not suffix:
        if abs(value) >= 1_000:
            return f"{value:,.0f}"
        if "." not in raw:
            return raw
    return f"{value:.2f}{suffix}"


def _repair_long_sentences(
    text: str,
    path: str,
) -> tuple[str, list[ValidationIssue]]:
    protected, replacements = _protect(text)
    sentences = SENTENCE_BOUNDARY.split(protected)
    repaired_sentences: list[str] = []
    issues: list[ValidationIssue] = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= TARGET_SENTENCE_WORDS:
            repaired_sentences.append(sentence)
            continue
        original_words = len(words)
        repaired_sentences.extend(_split_words(words))
        issues.append(
            ValidationIssue(
                code="long_sentence",
                severity=ValidationSeverity.REPAIRABLE,
                message=(
                    f"A {original_words}-word sentence was split locally toward the "
                    f"{TARGET_SENTENCE_WORDS}-word beginner target."
                ),
                location=path,
            )
        )
        if original_words > REVIEW_SENTENCE_WORDS:
            issues.append(
                ValidationIssue(
                    code="long_sentence_review",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"The original {original_words}-word sentence was repaired but "
                        "flagged for review."
                    ),
                    location=path,
                )
            )
    return _restore(" ".join(repaired_sentences), replacements), issues


def _split_words(words: list[str]) -> list[str]:
    parts: list[str] = []
    remaining = words
    while len(remaining) > TARGET_SENTENCE_WORDS:
        breakpoint = _breakpoint(remaining)
        left = remaining[:breakpoint]
        remaining = remaining[breakpoint:]
        left_text = " ".join(left).rstrip(" ,;:")
        if not left_text.endswith((".", "!", "?")):
            left_text += "."
        parts.append(left_text)
        if remaining:
            remaining[0] = _capitalize_token(remaining[0])
    if remaining:
        parts.append(" ".join(remaining))
    return parts


def _breakpoint(words: list[str]) -> int:
    lower = max(20, TARGET_SENTENCE_WORDS - 15)
    upper = min(TARGET_SENTENCE_WORDS, len(words) - 1)
    for index in range(upper, lower - 1, -1):
        previous = words[index - 1]
        current = words[index].lower().strip(" ,;:")
        if previous.endswith((",", ";", ":")):
            return index
        if current in {"and", "but", "because", "while", "although", "which"}:
            return index
    return upper


def _capitalize_token(token: str) -> str:
    for index, character in enumerate(token):
        if character.isalpha():
            return token[:index] + character.upper() + token[index + 1 :]
    return token


def _protect(text: str) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        key = f"PROTECTEDTOKEN{len(replacements)}"
        replacements[key] = match.group(0)
        return key

    return PROTECTED_PATTERN.sub(replace, text), replacements


def _restore(text: str, replacements: dict[str, str]) -> str:
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text
