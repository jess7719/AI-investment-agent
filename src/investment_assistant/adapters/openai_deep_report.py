"""OpenAI Responses API adapter for the beginner-friendly deep report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from typing import Any

import httpx
import openai
from openai import OpenAI
from pydantic import ValidationError

from investment_assistant.domain.deep_report import (
    DeepResearchReport,
    GeneratedDeepResearchReport,
)
from investment_assistant.domain.research import TokenUsage
from investment_assistant.ports.research import (
    MalformedResearchResponseError,
    ResearchAuthenticationError,
    ResearchBillingError,
    ResearchConnectionError,
    ResearchRateLimitError,
    ResearchTransportError,
)

DEEP_REPORT_MAX_OUTPUT_TOKENS = 6_000
DEEP_REPORT_SYSTEM_INSTRUCTIONS = """You are an evidence-grounded investment research
assistant writing for an intelligent beginner. Use only the structured application evidence.
Do not use web search, tools, external knowledge, or unstated article or filing content.

Produce a compact, decision-useful report under 3,500 output tokens:
- Use short, direct sentences and plain English. Explain necessary finance terms immediately.
- conclusion.summary contains at most five short sentences and one clear stance.
- key_evidence contains at most five genuinely useful facts. Each fact uses exactly one supplied
  reference. Do not repeat the same fact elsewhere.
- Keep all ordinary lists to three items or fewer. Do not fill lists with weak material.
- Give concise base, bull, and bear cases for 1-2 weeks. Probability ranges are subjective
  analytical estimates, not guaranteed predictions.
- Give concise 1-3 month cases and a long-term thesis without repeating the near-term section.
- Put uncertainty mainly in limitations, with no more than five items.

Evidence rules:
- Use [Market Data] only for supplied market, comparison, fundamental, and valuation data.
- Treat evidence_depth literally. Never imply full article or filing access when only an
  extract, summary, metadata description, or headline was supplied.
- Never state that news caused a price move unless the supplied evidence proves causation.
- Never introduce a number absent from the cited evidence or copy a long source passage.
- Mark unavailable, stale, estimated, or unreliable data once, without repeated disclaimers.
- Treat only metrics explicitly listed as decision_grade_metrics as usable fundamental or
  valuation evidence. Never use excluded third-party metrics to justify an investment view.
- Prefer official SEC or verified investor-relations figures. When a discrepancy is supplied,
  use the official value and state that the third-party value conflicts.
- For every valuation metric, state its supplied calculation inputs: market capitalisation,
  revenue period, shares outstanding, price date, calculation method, and resulting ratio.
- Do not classify the long-term thesis as weakened mainly from unverified third-party figures.

Asset-specific rule for asset_type=commodity_etp:
- Do not treat company revenue, earnings, operating margin, P/E, company free cash flow, or
  company balance-sheet valuation as required evidence or limitations.
- Replace equity valuation with the underlying commodity context, futures-curve structure,
  roll yield, tracking quality, product costs, currency exposure, and collateral/counterparty
  structure. Treat the named Brent or oil benchmark as primary; SPY is optional macro context.
- Assess only verified recent supplied evidence about supply, demand, inventories, the dollar,
  rates/growth, and geopolitics. Missing categories must not be filled with external knowledge.
- Explain contango (later futures cost more) as a potential negative roll yield and backwardation
  (later futures cost less) as a potential positive roll yield. If the curve is unavailable, say
  its direction cannot be assessed.
- Never infer poor liquidity from low exchange volume alone. Separate exchange volume, bid-ask
  spread, underlying-market liquidity, and market-maker liquidity.
- The long-term thesis must address structural oil supply/demand, the supplied curve and roll
  evidence, oil-price drivers, product costs, and likely tracking efficiency.
- Explain that a commodity ETC has no corporate earnings compounding and can face roll costs,
  commodity-cycle risk, and long periods of weak real returns. Long holding is not automatically
  preferable. Interpret confidence.fundamentals as confidence in commodity and product-structure
  evidence, not corporate accounts.

Investor action:
- Use only supplied investor-profile values and never infer missing values.
- Respect an intentionally small starter position and a preference against frequent trading
  when those facts are supplied.
- Choose only allowed action enum values. Do not issue an automatic buy or sell order.
- State what evidence should be seen before adding and give one direct reason.

Safety and quality:
- Do not guarantee returns, claim certainty about future prices, invent facts, give a target
  price, or claim to be a regulated financial adviser.
- Confidence describes evidence quality, not certainty about future prices.
- Avoid repeated disclaimers, repeated facts, formal wording, and vague advice.
- Return only the structured schema. Aim well below the 6,000-token safety ceiling.
"""


@dataclass(frozen=True, slots=True)
class DeepInputTokenEstimate:
    evidence_tokens: int
    instruction_tokens: int
    schema_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class MalformedDeepAPIResponse:
    model: str
    usage: TokenUsage
    response_id: str | None
    request_timestamp: datetime
    raw_output: str
    response_status: str | None = None
    incomplete_details: object | None = None


class ReceivedMalformedDeepResponseError(MalformedResearchResponseError):
    def __init__(self, receipt: MalformedDeepAPIResponse) -> None:
        super().__init__("OpenAI returned malformed structured deep-report content.")
        self.receipt = receipt


def estimate_deep_input_tokens(evidence_json: str) -> DeepInputTokenEstimate:
    """Approximate request tokens without constructing a client or making an API call."""

    schema_json = json.dumps(
        DeepResearchReport.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    evidence_tokens = _characters_to_tokens(len(evidence_json))
    instruction_tokens = _characters_to_tokens(len(DEEP_REPORT_SYSTEM_INSTRUCTIONS))
    schema_tokens = _characters_to_tokens(len(schema_json))
    return DeepInputTokenEstimate(
        evidence_tokens=evidence_tokens,
        instruction_tokens=instruction_tokens,
        schema_tokens=schema_tokens,
        total_tokens=evidence_tokens + instruction_tokens + schema_tokens,
    )


class OpenAIDeepResearchReasoner:
    """Make exactly one structured Responses API call without an SDK retry."""

    def __init__(
        self,
        api_key: str,
        model: str,
        client: Any | None = None,
        max_output_tokens: int | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds
        timeout = httpx.Timeout(timeout_seconds, connect=min(30.0, timeout_seconds))
        self._client = client or OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
        self._max_output_tokens = (
            configured_deep_max_output_tokens(str(max_output_tokens))
            if max_output_tokens is not None
            else configured_deep_max_output_tokens()
        )

    def generate(self, evidence_json: str) -> GeneratedDeepResearchReport:
        request_timestamp = datetime.now(timezone.utc)
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=DEEP_REPORT_SYSTEM_INSTRUCTIONS,
                input=evidence_json,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "deep_research_report",
                        "strict": True,
                        "schema": DeepResearchReport.model_json_schema(),
                    }
                },
                max_output_tokens=self._max_output_tokens,
                store=False,
            )
        except openai.AuthenticationError as error:
            raise ResearchAuthenticationError(
                "OpenAI authentication failed. Check OPENAI_API_KEY in .env."
            ) from error
        except openai.RateLimitError as error:
            if _looks_like_billing_error(error):
                raise ResearchBillingError(
                    "OpenAI reported insufficient credit, quota, or a billing problem."
                ) from error
            raise ResearchRateLimitError(
                "OpenAI rate limit reached. Wait before trying again."
            ) from error
        except openai.APITimeoutError as error:
            category, ambiguous = _timeout_category(error)
            raise ResearchTransportError(
                _transport_message(category),
                category=category,
                ambiguous_billing=ambiguous,
                request_id=_exception_request_id(error),
            ) from error
        except openai.APIConnectionError as error:
            category, ambiguous = _connection_category(error)
            raise ResearchTransportError(
                _transport_message(category),
                category=category,
                ambiguous_billing=ambiguous,
                request_id=_exception_request_id(error),
            ) from error
        except openai.APIStatusError as error:
            if error.status_code == 402 or _looks_like_billing_error(error):
                raise ResearchBillingError(
                    "OpenAI reported insufficient credit, quota, or a billing problem."
                ) from error
            if error.status_code in {408, 504}:
                raise ResearchTransportError(
                    "OpenAI returned an API timeout response after request submission.",
                    category="api_timeout_response",
                    ambiguous_billing=True,
                    request_id=_exception_request_id(error),
                ) from error
            raise ResearchConnectionError(
                f"OpenAI request failed with status {error.status_code}."
            ) from error
        except openai.APIResponseValidationError as error:
            raise MalformedResearchResponseError(
                "OpenAI SDK could not decode the HTTP response."
            ) from error
        usage = getattr(response, "usage", None)
        input_details = getattr(usage, "input_tokens_details", None)
        receipt = MalformedDeepAPIResponse(
            model=str(getattr(response, "model", None) or self._model),
            usage=TokenUsage(
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
                cached_input_tokens=getattr(input_details, "cached_tokens", None),
            ),
            response_id=getattr(response, "id", None),
            request_timestamp=request_timestamp,
            raw_output=str(getattr(response, "output_text", "") or ""),
            response_status=(str(response.status) if getattr(response, "status", None) else None),
            incomplete_details=_serializable_details(getattr(response, "incomplete_details", None)),
        )
        try:
            report = DeepResearchReport.model_validate_json(receipt.raw_output)
        except ValidationError as error:
            raise ReceivedMalformedDeepResponseError(receipt) from error
        return GeneratedDeepResearchReport(
            report=report,
            model=receipt.model,
            usage=receipt.usage,
            response_id=receipt.response_id,
            request_timestamp=receipt.request_timestamp,
            raw_output=receipt.raw_output,
            response_status=receipt.response_status,
            incomplete_details=receipt.incomplete_details,
        )


def configured_deep_max_output_tokens(raw: str | None = None) -> int:
    """Read a lower configurable allowance while retaining a 6,000-token hard ceiling."""

    value = raw if raw is not None else os.getenv("OPENAI_DEEP_MAX_OUTPUT_TOKENS")
    if value is None or not value.strip():
        return DEEP_REPORT_MAX_OUTPUT_TOKENS
    try:
        parsed = int(value)
    except ValueError:
        return DEEP_REPORT_MAX_OUTPUT_TOKENS
    return max(1_000, min(parsed, DEEP_REPORT_MAX_OUTPUT_TOKENS))


def _serializable_details(value: object) -> object | None:
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def _looks_like_billing_error(error: Exception) -> bool:
    code = str(getattr(error, "code", "") or "").lower()
    message = str(error).lower()
    return any(
        indicator in code or indicator in message
        for indicator in ("insufficient_quota", "quota", "billing", "credit")
    )


def _characters_to_tokens(characters: int) -> int:
    return max(1, (characters + 3) // 4)


def _cause_chain(error: BaseException) -> list[BaseException]:
    result: list[BaseException] = []
    current = error.__cause__ or error.__context__
    while current is not None and current not in result:
        result.append(current)
        current = current.__cause__ or current.__context__
    return result


def _timeout_category(error: BaseException) -> tuple[str, bool]:
    causes = _cause_chain(error)
    if any(isinstance(item, httpx.ConnectTimeout) for item in causes):
        return "connection_timeout", False
    return "read_timeout_after_submission", True


def _connection_category(error: BaseException) -> tuple[str, bool]:
    causes = _cause_chain(error)
    if any(isinstance(item, (httpx.ConnectError, httpx.ConnectTimeout)) for item in causes):
        return "connection_timeout", False
    return "local_network_interruption", True


def _exception_request_id(error: BaseException) -> str | None:
    direct = getattr(error, "request_id", None)
    if direct:
        return str(direct)
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("x-request-id")
        if value:
            return str(value)
    return None


def _transport_message(category: str) -> str:
    return {
        "connection_timeout": "OpenAI connection timed out before a response was received.",
        "read_timeout_after_submission": (
            "OpenAI read timed out after request submission; billing status is unknown."
        ),
        "local_network_interruption": (
            "The local network connection was interrupted after request submission; "
            "billing status is unknown."
        ),
    }[category]
