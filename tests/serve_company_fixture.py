"""Local visual-QA server using labelled fixtures only; never calls providers."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_single_company import CONFIG, FakeClient, NOW  # noqa: E402
from investment_assistant.single_company.analysis import current_report  # noqa: E402
from investment_assistant.single_company.pipeline import ingest  # noqa: E402
from investment_assistant.single_company.storage import CompanyStore  # noqa: E402
from investment_assistant.single_company import web  # noqa: E402
from investment_assistant.web.app import create_app  # noqa: E402


if __name__ == "__main__":
    import uvicorn
    root = Path(__file__).resolve().parents[1] / "test-output-company-browser"
    store = CompanyStore(root / "data" / "single-company")
    ingest(store, CONFIG, full=True, client=FakeClient(), now=NOW)
    original_report = current_report

    def fixture_report(store, config):
        result = original_report(store, config, now=NOW)
        result["company"]["name"] = "NVIDIA · OFFLINE FIXTURE"
        return result

    web.current_report = fixture_report
    app = create_app(root)
    uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False)
