"""Disposable offline browser acceptance server. Run from the repository root."""

from datetime import date, timedelta
from math import sin
from pathlib import Path

import uvicorn

from investment_assistant.services.research_service import ResearchService
from investment_assistant.web.app import create_app
from investment_assistant.web.charts import aligned_rows
from test_research_service import FakeMarketData, FakeNews, FakeReasoner, sample_article


class FixtureCharts:
    def get(self, ticker, window):
        rows = [{"date": (date.today() - timedelta(days=day)).isoformat(),
                 "close": round(120 - day * .18 + sin(day / 8) * 3, 2),
                 "volume": 2000000 + day * 78000} for day in range(
                     {"1M": 31, "3M": 93, "1Y": 365}[window], -1, -1)
                if (date.today() - timedelta(days=day)).weekday() < 5]
        benchmark = [{**r, "close": 100 + i * .1} for i, r in enumerate(rows)]
        return {"ticker": ticker, "rows": rows, "comparison": aligned_rows(rows, benchmark),
                "currency": "USD", "benchmark": "QA comparison", "benchmark_currency": "USD",
                "source": "Offline QA fixture — synthetic observations",
                "retrieved_at": f"{date.today()}T12:00:00Z", "adjustment": "Synthetic adjusted prices.",
                "context": "Synthetic data for interface verification only.",
                "comparison_status": "Available"}

    def financial(self, ticker):
        return {"kind": "unavailable", "message": "Offline QA: financial provider is disabled."}


if __name__ == "__main__":
    root = Path("test-output-browser").resolve()
    root.mkdir(exist_ok=True)
    app = create_app(root, chart_service=FixtureCharts(),
                     search_provider=lambda query: [],
                     research_factory=lambda settings: ResearchService(
                         FakeMarketData(), FakeNews((sample_article(),)), root / "reports", date.today()),
                     reasoner_factory=lambda *args: FakeReasoner())
    uvicorn.run(app, host="127.0.0.1", port=8001, access_log=False)
