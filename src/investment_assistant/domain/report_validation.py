"""Severity-labelled report validation results."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ValidationSeverity(StrEnum):
    FATAL = "fatal"
    REPAIRABLE = "repairable locally"
    WARNING = "warning only"


class ValidationStatus(StrEnum):
    PASSED = "passed"
    REPAIRED = "repaired"
    REJECTED = "rejected"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=1, max_length=80)]
    severity: ValidationSeverity
    message: Annotated[str, Field(min_length=1, max_length=500)]
    location: Annotated[str, Field(min_length=1, max_length=300)] | None = None


class ValidationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    issues: list[ValidationIssue]

    @property
    def fatal_issues(self) -> list[ValidationIssue]:
        return [
            issue for issue in self.issues if issue.severity is ValidationSeverity.FATAL
        ]

    @property
    def repair_issues(self) -> list[ValidationIssue]:
        return [
            issue
            for issue in self.issues
            if issue.severity is ValidationSeverity.REPAIRABLE
        ]

    @property
    def warning_issues(self) -> list[ValidationIssue]:
        return [
            issue for issue in self.issues if issue.severity is ValidationSeverity.WARNING
        ]
