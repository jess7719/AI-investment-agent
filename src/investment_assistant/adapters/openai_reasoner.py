"""Official OpenAI Responses API adapter for validated research reports."""

from __future__ import annotations

from typing import Any

import openai
from openai import OpenAI
from pydantic import ValidationError

from investment_assistant.domain.research import (
    GeneratedResearchReport,
    ResearchReport,
    TokenUsage,
)
from investment_assistant.ports.research import (
    MalformedResearchResponseError,
    ResearchAuthenticationError,
    ResearchBillingError,
    ResearchConnectionError,
    ResearchRateLimitError,
)

MAX_OUTPUT_TOKENS = 3_000
SYSTEM_INSTRUCTIONS = """You are an evidence-grounded investment research assistant.
Analyse only the structured evidence supplied by the application. No web search, tools,
external knowledge, or unstated article/filing content may be used.

Evidence rules:
- [Market Data] contains verified numerical market observations.
- [News N] contains only a headline, publisher, date, classification, scores, and possibly
  a source-provided summary. Never imply the full article was read.
- [Filing N] contains filing metadata and possibly selected structured Form 4 fields.
  Never imply the full filing was read.
- Each information item includes an evidence_depth label. Treat it literally: official filing
  extract, official company release, full public article extract, source-provided summary,
  public metadata description, user-provided notes, or headline only. Never claim a deeper
  evidence type than the supplied label.
- Content extracts are deliberately bounded. Paraphrase only short necessary points and never
  reproduce an article extract at length.
- Label headline-only conclusions as headline and metadata, filing conclusions as filing
  metadata, and structured ownership conclusions as structured Form 4 metadata.
- Every factual or interpretive statement must cite supplied references. Never invent a
  reference. Never label anything as fuller evidence in this report.
- Separate verified observations from interpretations and uncertainty.
- Do not state that news caused a price move unless the supplied evidence proves causation.
- If evidence is weak, incomplete, conflicting, or headline-only, say so explicitly.

Investment-safety rules:
- Do not guarantee returns, predict a certain future price, provide a target price, or issue
  automatic buy/sell instructions.
- Do not claim to be a regulated financial adviser.
- Confidence means evidence quality, not confidence in future price prediction.
- Keep the report concise and within the structured schema limits.
"""


class OpenAIResearchReasoner:
    """Call Responses API once, with at most one SDK-managed retry."""

    def __init__(self, api_key: str, model: str, client: Any | None = None) -> None:
        self._model = model
        self._client = client or OpenAI(
            api_key=api_key,
            timeout=60.0,
            max_retries=1,
        )

    def generate(self, evidence_json: str) -> GeneratedResearchReport:
        try:
            response = self._client.responses.parse(
                model=self._model,
                instructions=SYSTEM_INSTRUCTIONS,
                input=evidence_json,
                text_format=ResearchReport,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
            report = response.output_parsed
            if not isinstance(report, ResearchReport):
                raise MalformedResearchResponseError(
                    "OpenAI returned no validated structured research report."
                )
            usage = getattr(response, "usage", None)
            return GeneratedResearchReport(
                report=report,
                model=self._model,
                usage=TokenUsage(
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None),
                ),
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
        except (openai.APITimeoutError, openai.APIConnectionError) as error:
            raise ResearchConnectionError(
                "OpenAI request timed out or the network connection failed."
            ) from error
        except openai.APIStatusError as error:
            if error.status_code == 402 or _looks_like_billing_error(error):
                raise ResearchBillingError(
                    "OpenAI reported insufficient credit, quota, or a billing problem."
                ) from error
            raise ResearchConnectionError(
                f"OpenAI request failed with status {error.status_code}."
            ) from error
        except (
            ValidationError,
            openai.APIResponseValidationError,
            openai.LengthFinishReasonError,
            openai.ContentFilterFinishReasonError,
        ) as error:
            raise MalformedResearchResponseError(
                "OpenAI returned a malformed structured response."
            ) from error


def _looks_like_billing_error(error: Exception) -> bool:
    code = str(getattr(error, "code", "") or "").lower()
    message = str(error).lower()
    indicators = ("insufficient_quota", "quota", "billing", "credit")
    return any(indicator in code or indicator in message for indicator in indicators)
