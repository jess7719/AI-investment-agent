"""Offline browser QA: fixture prices for NVDA/AAPL/MSFT; real sourced directory."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investment_assistant.single_company.directory import Directory  # noqa: E402
from investment_assistant.single_company.pipeline import ingest  # noqa: E402
from investment_assistant.single_company.storage import CompanyStore  # noqa: E402
from investment_assistant.single_company import web  # noqa: E402
from investment_assistant.web.app import create_app  # noqa: E402
from test_multi_company import MultiClient, sid, verify  # noqa: E402
from test_single_company import CONFIG, NOW  # noqa: E402


if __name__ == "__main__":
    import asyncio
    import uvicorn
    root = Path(__file__).resolve().parents[1] / "test-output-multi-browser"
    store = CompanyStore(root / "data" / "single-company")
    verify(store, "AAPL", "MSFT")
    for ticker in ("NVDA", "AAPL", "MSFT"):
        config = Directory(store).get(sid(store, ticker)).config(CONFIG)
        ingest(store, config, client=MultiClient(config), now=NOW)
    web.CompanyConfig.from_environment = lambda: CONFIG
    app = create_app(root)
    app.state.fixture_data = True

    @app.middleware("http")
    async def delayed_report(request, call_next):
        if request.url.path.endswith("/report"):
            await asyncio.sleep(.5 if sid(store, "AAPL") in request.url.path else .05)
        return await call_next(request)

    uvicorn.run(app, host="127.0.0.1", port=8767, access_log=False)
