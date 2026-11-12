"""Boundary for a structured daily-news reasoning engine."""

from __future__ import annotations

from typing import Protocol

from investment_assistant.domain.news_report import GeneratedDailyNewsReport


class DailyNewsReasoner(Protocol):
    def generate(self, evidence_json: str) -> GeneratedDailyNewsReport: ...
