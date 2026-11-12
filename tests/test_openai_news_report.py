from types import SimpleNamespace
from typing import Any

from investment_assistant.adapters.openai_news_report import (
    NEWS_REPORT_MAX_OUTPUT_TOKENS,
    OpenAIDailyNewsReasoner,
)
from investment_assistant.domain.news_report import DailyNewsReport
from tests.test_daily_news_report import report_data


class FakeResponses:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] | None = None

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.result


def test_news_report_uses_responses_schema_without_browsing() -> None:
    responses = FakeResponses(
        SimpleNamespace(
            output_parsed=DailyNewsReport.model_validate(report_data()),
            usage=SimpleNamespace(input_tokens=900, output_tokens=500, total_tokens=1400),
        )
    )
    client = SimpleNamespace(responses=responses)

    generated = OpenAIDailyNewsReasoner(
        "not-a-real-key", "gpt-test", client
    ).generate("{}")

    assert generated.usage.total_tokens == 1400
    assert responses.kwargs is not None
    assert responses.kwargs["text_format"] is DailyNewsReport
    assert responses.kwargs["max_output_tokens"] == NEWS_REPORT_MAX_OUTPUT_TOKENS
    assert responses.kwargs["store"] is False
    assert "tools" not in responses.kwargs
    assert "web_search" not in str(responses.kwargs).lower()
