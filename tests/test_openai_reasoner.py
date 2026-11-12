from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from investment_assistant.adapters.openai_reasoner import (
    MAX_OUTPUT_TOKENS,
    OpenAIResearchReasoner,
)
from investment_assistant.domain.research import ResearchReport
from investment_assistant.ports.research import (
    MalformedResearchResponseError,
    ResearchAuthenticationError,
    ResearchBillingError,
    ResearchConnectionError,
    ResearchRateLimitError,
)


def valid_report(reference: str = "[News 1]") -> ResearchReport:
    point = {
        "statement": "The supplied evidence is limited.",
        "references": [reference],
        "evidence_basis": "headline and metadata",
        "uncertainty": "No article body was supplied.",
    }
    return ResearchReport.model_validate(
        {
            "executive_summary": [point],
            "verified_facts": [point],
            "market_movement": [
                {
                    **point,
                    "references": ["[Market Data]"],
                    "evidence_basis": "market data",
                }
            ],
            "important_information": [
                {
                    "reference": reference,
                    "source": "Example News",
                    "classification": "factual financial reporting",
                    "why_it_may_matter": "It may warrant further research.",
                    "evidence_basis": "headline and metadata",
                    "uncertainty": "Only metadata was supplied.",
                }
            ],
            "possible_short_term_implications": [point],
            "possible_medium_and_long_term_implications": [point],
            "bull_case": [point],
            "bear_case": [point],
            "key_risks": [point],
            "upcoming_factors_to_monitor": [point],
            "evidence_gaps": ["Article and filing body text were not supplied."],
            "investment_thesis_status": "insufficient evidence",
            "suggested_investor_responses": ["research further"],
            "confidence_level": "low",
            "confidence_rationale": "The evidence is headline-only.",
        }
    )


class FakeResponses:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.kwargs: dict[str, Any] | None = None

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.result


class FakeClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


def test_uses_responses_parse_with_schema_and_no_tools() -> None:
    responses = FakeResponses(
        SimpleNamespace(
            output_parsed=valid_report(),
            usage=SimpleNamespace(input_tokens=1200, output_tokens=700, total_tokens=1900),
        )
    )
    reasoner = OpenAIResearchReasoner("not-a-real-key", "gpt-test", FakeClient(responses))

    generated = reasoner.generate('{"ticker":"SPCX"}')

    assert generated.usage.total_tokens == 1900
    assert responses.kwargs is not None
    assert responses.kwargs["model"] == "gpt-test"
    assert responses.kwargs["text_format"] is ResearchReport
    assert responses.kwargs["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert responses.kwargs["store"] is False
    assert "tools" not in responses.kwargs
    assert "web_search" not in str(responses.kwargs).lower()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            openai.AuthenticationError(
                "invalid key",
                response=httpx.Response(401, request=httpx.Request("POST", "https://api.test")),
                body=None,
            ),
            ResearchAuthenticationError,
        ),
        (
            openai.RateLimitError(
                "insufficient_quota",
                response=httpx.Response(429, request=httpx.Request("POST", "https://api.test")),
                body={"code": "insufficient_quota"},
            ),
            ResearchBillingError,
        ),
        (
            openai.RateLimitError(
                "requests too quickly",
                response=httpx.Response(429, request=httpx.Request("POST", "https://api.test")),
                body=None,
            ),
            ResearchRateLimitError,
        ),
        (
            openai.APITimeoutError(httpx.Request("POST", "https://api.test")),
            ResearchConnectionError,
        ),
    ],
)
def test_maps_sdk_errors_to_safe_messages(error: Exception, expected: type[Exception]) -> None:
    reasoner = OpenAIResearchReasoner(
        "not-a-real-key", "gpt-test", FakeClient(FakeResponses(error=error))
    )

    with pytest.raises(expected) as caught:
        reasoner.generate("{}")

    assert "not-a-real-key" not in str(caught.value)


def test_rejects_missing_parsed_output() -> None:
    reasoner = OpenAIResearchReasoner(
        "not-a-real-key",
        "gpt-test",
        FakeClient(FakeResponses(SimpleNamespace(output_parsed=None, usage=None))),
    )

    with pytest.raises(MalformedResearchResponseError):
        reasoner.generate("{}")
