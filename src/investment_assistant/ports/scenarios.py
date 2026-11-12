"""Scenario inputs are independent of any LLM or personal-profile provider."""

from datetime import datetime
from typing import Protocol

from investment_assistant.domain.scenarios import ScenarioEvidencePackage


class ScenarioEvidenceSource(Protocol):
    def load(self, ticker: str, cutoff: datetime) -> ScenarioEvidencePackage: ...
