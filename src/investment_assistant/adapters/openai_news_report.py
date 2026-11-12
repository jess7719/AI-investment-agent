"""OpenAI Responses API adapter for the structured daily news report."""

from __future__ import annotations

from typing import Any

import openai
from openai import OpenAI
from pydantic import ValidationError

from investment_assistant.domain.news_report import (
    DailyNewsReport,
    GeneratedDailyNewsReport,
)
from investment_assistant.domain.research import TokenUsage
from investment_assistant.ports.research import (
    MalformedResearchResponseError,
    ResearchAuthenticationError,
    ResearchBillingError,
    ResearchConnectionError,
    ResearchRateLimitError,
)

NEWS_REPORT_MAX_OUTPUT_TOKENS = 2_800
NEWS_REPORT_SYSTEM_INSTRUCTIONS = """You write a daily stock-news report for an intelligent
beginner investor. Use only the structured evidence in the request. Do not use web search,
tools, external knowledge, or unstated article or filing content.

Analyse events, not article counts:
- Use only event_selection.main_event_clusters for important_events and preserve each cluster_id.
- Choose no more than three distinct events. Never turn related reports into separate events.
- Explain what is genuinely new and how it could affect revenue, costs, profit, cash flow,
  valuation, growth, confidence, share supply, regulation, management, or execution risk.
- Use selection_role=noise items only in noise_to_ignore. Explain the concrete reason each is
  low value. Do not invent noise merely to fill the section.
- Prefer direct official evidence. Opinion may describe sentiment but cannot establish facts.

Evidence rules:
- Cite every factual or material interpretive statement with supplied references such as
  [News 1], [Filing 1], or [Market Data]. Never invent references.
- Treat evidence_depth literally. Never imply you read more than was supplied.
- Headline-only claims must stay narrow. Metadata descriptions are not full articles.
- Bounded extracts may be paraphrased briefly but must not be reproduced at length.
- Do not claim that an event caused a price move unless the supplied evidence proves it.
- Distinguish facts, interpretation, and prediction through clear wording.

Writing rules:
- Use short, direct, plain-English sentences. Explain necessary finance terms immediately.
- Do not merely restate headlines. Explain why the event matters and give a qualified view.
- Avoid compliance-memo language, jargon, repetition, and unsupported generic advice.
- The first section contains at most five sentences and includes the news-flow stance, main
  development, long-term-versus-sentiment judgement, and one specific action.
- Give one final stance. Uncertainty belongs mainly in evidence-strength explanations.
- Use supplied investor-profile values when relevant. Never infer missing profile fields.
- If the profile is unconfigured, say so and provide both owner and non-owner action frameworks
  without assuming holdings, cost basis, position size, or risk tolerance.
- A specific action trigger names the fact that would justify reassessing the decision.

Safety rules:
- Do not guarantee returns, predict a certain price, give a target price, or issue automatic
  buy/sell instructions. Do not claim to be a regulated financial adviser.
- Never treat a politician's purchase, analyst quote, or social-media comment as proof that the
  company is undervalued.
- Stay within the structured schema and report-length limits.
"""


class OpenAIDailyNewsReasoner:
    """Make one structured Responses API call with at most one SDK retry."""

    def __init__(self, api_key: str, model: str, client: Any | None = None) -> None:
        self._model = model
        self._client = client or OpenAI(api_key=api_key, timeout=60.0, max_retries=1)

    def generate(self, evidence_json: str) -> GeneratedDailyNewsReport:
        try:
            response = self._client.responses.parse(
                model=self._model,
                instructions=NEWS_REPORT_SYSTEM_INSTRUCTIONS,
                input=evidence_json,
                text_format=DailyNewsReport,
                max_output_tokens=NEWS_REPORT_MAX_OUTPUT_TOKENS,
                store=False,
            )
            report = response.output_parsed
            if not isinstance(report, DailyNewsReport):
                raise MalformedResearchResponseError(
                    "OpenAI returned no validated structured daily news report."
                )
            usage = getattr(response, "usage", None)
            return GeneratedDailyNewsReport(
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
                "OpenAI returned a malformed structured daily news report."
            ) from error


def _looks_like_billing_error(error: Exception) -> bool:
    code = str(getattr(error, "code", "") or "").lower()
    message = str(error).lower()
    return any(
        indicator in code or indicator in message
        for indicator in ("insufficient_quota", "quota", "billing", "credit")
    )
