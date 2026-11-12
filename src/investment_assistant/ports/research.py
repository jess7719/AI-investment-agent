"""Boundary for a structured AI research engine."""

from __future__ import annotations

from typing import Protocol

from investment_assistant.domain.research import GeneratedResearchReport


class ResearchReasoner(Protocol):
    def generate(self, evidence_json: str) -> GeneratedResearchReport: ...


class ResearchError(RuntimeError):
    """Base class for safe, user-facing research errors."""


class MissingAPIKeyError(ResearchError):
    pass


class ResearchAuthenticationError(ResearchError):
    pass


class ResearchBillingError(ResearchError):
    pass


class ResearchRateLimitError(ResearchError):
    pass


class ResearchConnectionError(ResearchError):
    pass


class ResearchTransportError(ResearchConnectionError):
    """Secret-free transport diagnostics for a single submitted request."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        ambiguous_billing: bool,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.ambiguous_billing = ambiguous_billing
        self.request_id = request_id


class MalformedResearchResponseError(ResearchError):
    pass


class ResearchDataUnavailableError(ResearchError):
    pass
