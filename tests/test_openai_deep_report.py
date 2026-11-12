from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from investment_assistant.adapters.openai_deep_report import (
    DEEP_REPORT_MAX_OUTPUT_TOKENS,
    OpenAIDeepResearchReasoner,
    ReceivedMalformedDeepResponseError,
    configured_deep_max_output_tokens,
    estimate_deep_input_tokens,
)
from investment_assistant.domain.deep_report import DeepResearchReport
from investment_assistant.ports.research import ResearchTransportError
from tests.test_deep_report import deep_report_data


class FakeResponses:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.result


def test_deep_report_uses_responses_schema_without_tools() -> None:
    responses = FakeResponses(
        SimpleNamespace(
            id="resp_test_123",
            model="gpt-test-snapshot",
            status="completed",
            incomplete_details=None,
            output_text=DeepResearchReport.model_validate(deep_report_data()).model_dump_json(),
            usage=SimpleNamespace(
                input_tokens=3000,
                input_tokens_details=SimpleNamespace(cached_tokens=750),
                output_tokens=1800,
                total_tokens=4800,
            ),
        )
    )
    client = SimpleNamespace(responses=responses)

    generated = OpenAIDeepResearchReasoner("not-a-real-key", "gpt-test", client).generate("{}")

    assert generated.usage.total_tokens == 4800
    assert generated.usage.cached_input_tokens == 750
    assert generated.model == "gpt-test-snapshot"
    assert generated.response_id == "resp_test_123"
    assert generated.request_timestamp is not None
    assert generated.raw_output is not None
    assert '"conclusion"' in generated.raw_output
    assert generated.response_status == "completed"
    assert responses.kwargs is not None
    text = responses.kwargs["text"]
    assert text["format"]["type"] == "json_schema"
    assert text["format"]["strict"] is True
    assert responses.kwargs["max_output_tokens"] == DEEP_REPORT_MAX_OUTPUT_TOKENS
    assert responses.kwargs["store"] is False
    assert "tools" not in responses.kwargs
    assert "web_search" not in str(responses.kwargs).lower()
    assert set(text["format"]["schema"]["properties"]) == {
        "conclusion",
        "key_evidence",
        "near_term_view",
        "medium_term_view",
        "long_term_thesis",
        "investor_action",
        "thesis_triggers",
        "confidence",
        "limitations",
    }


def test_malformed_output_retains_usage_and_response_metadata() -> None:
    responses = FakeResponses(
        SimpleNamespace(
            id="resp_malformed",
            model="gpt-test-snapshot",
            status="incomplete",
            incomplete_details=SimpleNamespace(
                model_dump=lambda mode: {"reason": "max_output_tokens"}
            ),
            output_text='{"not":"a deep report"}',
            usage=SimpleNamespace(
                input_tokens=2200,
                input_tokens_details=SimpleNamespace(cached_tokens=500),
                output_tokens=50,
                total_tokens=2250,
            ),
        )
    )

    with pytest.raises(ReceivedMalformedDeepResponseError) as caught:
        OpenAIDeepResearchReasoner(
            "not-a-real-key",
            "gpt-test",
            SimpleNamespace(responses=responses),
        ).generate("{}")

    receipt = caught.value.receipt
    assert receipt.response_id == "resp_malformed"
    assert receipt.usage.input_tokens == 2200
    assert receipt.usage.cached_input_tokens == 500
    assert receipt.raw_output == '{"not":"a deep report"}'
    assert receipt.response_status == "incomplete"
    assert receipt.incomplete_details == {"reason": "max_output_tokens"}


def test_output_allowance_is_configurable_but_capped_at_six_thousand() -> None:
    assert configured_deep_max_output_tokens(None) == DEEP_REPORT_MAX_OUTPUT_TOKENS
    assert configured_deep_max_output_tokens("3500") == 3500
    assert configured_deep_max_output_tokens("99999") == 6000
    assert configured_deep_max_output_tokens("invalid") == 6000


def test_input_estimate_includes_evidence_instructions_and_schema_without_client() -> None:
    estimate = estimate_deep_input_tokens('{"ticker":"SPCX"}')

    assert estimate.evidence_tokens > 0
    assert estimate.instruction_tokens > 0
    assert estimate.schema_tokens > 0
    assert estimate.total_tokens == (
        estimate.evidence_tokens + estimate.instruction_tokens + estimate.schema_tokens
    )


class ErrorResponses:
    def __init__(self, error_factory: Any) -> None:
        self.error_factory = error_factory
        self.calls = 0

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.error_factory()


def test_distinguishes_connect_timeout_without_retry() -> None:
    request = httpx.Request("POST", "https://api.openai.test")

    def fail() -> None:
        try:
            raise httpx.ConnectTimeout("connect", request=request)
        except httpx.ConnectTimeout as cause:
            raise openai.APITimeoutError(request=request) from cause

    responses = ErrorResponses(fail)
    with pytest.raises(ResearchTransportError) as caught:
        OpenAIDeepResearchReasoner(
            "not-a-real-key",
            "gpt-test",
            SimpleNamespace(responses=responses),
        ).generate("{}")

    assert caught.value.category == "connection_timeout"
    assert caught.value.ambiguous_billing is False
    assert responses.calls == 1


def test_distinguishes_read_timeout_after_submission_without_retry() -> None:
    request = httpx.Request("POST", "https://api.openai.test")

    def fail() -> None:
        try:
            raise httpx.ReadTimeout("read", request=request)
        except httpx.ReadTimeout as cause:
            error = openai.APITimeoutError(request=request)
            error.request_id = "req_read_timeout"
            raise error from cause

    responses = ErrorResponses(fail)
    with pytest.raises(ResearchTransportError) as caught:
        OpenAIDeepResearchReasoner(
            "not-a-real-key",
            "gpt-test",
            SimpleNamespace(responses=responses),
        ).generate("{}")

    assert caught.value.category == "read_timeout_after_submission"
    assert caught.value.ambiguous_billing is True
    assert caught.value.request_id == "req_read_timeout"
    assert responses.calls == 1


def test_distinguishes_local_network_interruption() -> None:
    request = httpx.Request("POST", "https://api.openai.test")

    def fail() -> None:
        try:
            raise httpx.ReadError("connection dropped", request=request)
        except httpx.ReadError as cause:
            raise openai.APIConnectionError(request=request) from cause

    with pytest.raises(ResearchTransportError) as caught:
        OpenAIDeepResearchReasoner(
            "not-a-real-key",
            "gpt-test",
            SimpleNamespace(responses=ErrorResponses(fail)),
        ).generate("{}")

    assert caught.value.category == "local_network_interruption"
    assert caught.value.ambiguous_billing is True


def test_distinguishes_api_timeout_response_and_preserves_request_id() -> None:
    request = httpx.Request("POST", "https://api.openai.test")
    response = httpx.Response(
        504,
        request=request,
        headers={"x-request-id": "req_gateway_timeout"},
    )

    def fail() -> None:
        raise openai.APIStatusError(
            "gateway timeout",
            response=response,
            body={"error": "timeout"},
        )

    with pytest.raises(ResearchTransportError) as caught:
        OpenAIDeepResearchReasoner(
            "not-a-real-key",
            "gpt-test",
            SimpleNamespace(responses=ErrorResponses(fail)),
        ).generate("{}")

    assert caught.value.category == "api_timeout_response"
    assert caught.value.ambiguous_billing is True
    assert caught.value.request_id == "req_gateway_timeout"
