"""Boundary for the structured deep-research reasoning engine."""

from __future__ import annotations

from typing import Protocol

from investment_assistant.domain.deep_report import GeneratedDeepResearchReport


class DeepResearchReasoner(Protocol):
    def generate(self, evidence_json: str) -> GeneratedDeepResearchReport: ...
