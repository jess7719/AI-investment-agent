"""Stored-only routes mounted inside the existing protected local workspace."""

from typing import Literal

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .analysis import current_report
from .config import CompanyConfig
from .storage import CompanyStore
from .directory import Directory, PILOT
from .facade import AnalysisFacade
from .jobs import enqueue, get_job


def mount_company(app, root, page, *, store=None, config=None):
    config = config or CompanyConfig.from_environment()
    store = store or CompanyStore(root / "data" / "single-company")
    app.state.company_store = store
    app.state.company_config = config
    from investment_assistant.config import contextual_data_root
    from investment_assistant.services.context_artifacts import ContextArtifactStore
    directory = Directory(store)
    app.state.company_directory = directory
    facade = AnalysisFacade(store, config, ContextArtifactStore(contextual_data_root(root)))

    def context(sid):
        try:
            return directory.get(sid)
        except KeyError:
            raise HTTPException(404, "Unknown security ID. Choose an active company from the directory.") from None

    def report_for(sid):
        selected = context(sid)
        if selected.identity["status"] != "active":
            raise HTTPException(410, "This security is retired. Its historical records are retained.")
        if selected.identity["instrument_type"] == "benchmark":
            raise HTTPException(422, "This instrument is a benchmark, not an operating-company report.")
        return facade.latest(sid)

    @app.get("/company")
    def dashboard(request: Request, security_id: str | None = None):
        sid = security_id or PILOT
        try:
            report = report_for(sid)
            resolution = None
        except HTTPException as exc:
            report, resolution = None, exc.detail
        return page(request, "company.html", report=report, security_id=sid,
                    resolution=resolution, directory=directory.search(), explicit_selection=security_id is not None)

    @app.get("/api/securities")
    def securities(q: str = Query("", max_length=100), filter: Literal["all", "ready", "saved"] = "all",
                   cursor: str | None = Query(None, max_length=1024), limit: int = Query(50, ge=1, le=100),
                   saved: str = Query("", max_length=12000)):
        return directory.search(q, filter, cursor, limit, saved.split(",") if saved else ())

    @app.get("/api/securities/{sid}")
    def security(sid: str):
        selected = context(sid)
        return {"security_id": sid, "issuer_id": selected.issuer_id, "identity": selected.identity,
                "provider_symbols": selected.provider_symbols, "financial_template": selected.financial_template,
                "preparation_allowed": selected.ready, "market_id": selected.market_id, "sector_id": selected.sector_id}

    @app.get("/api/securities/{sid}/analysis/latest")
    def scoped_latest(sid: str):
        report = report_for(sid)
        report.pop("chart", None)
        return report

    @app.get("/api/securities/{sid}/report")
    def report_fragment(request: Request, sid: str):
        report = report_for(sid)
        # A JSON envelope lets the browser verify identity before applying escaped server HTML.
        response = page(request, "company_report.html", report=report)
        return {"security_id": sid, "html": response.body.decode("utf-8"), "job": report["job"]}

    @app.get("/api/securities/{sid}/prices")
    def scoped_prices(sid: str, range: Literal["1m", "3m", "1y", "5y", "10y"] = "1y"):
        report = report_for(sid)
        size = {"1m": 21, "3m": 63, "1y": 253, "5y": 1261, "10y": 2521}[range]
        return {**{k: report.get(k) for k in ("security_id", "snapshot_id", "as_of", "sources", "company", "data_status",
                                             "return_convention", "adjustments_verified", "input_versions")},
                "unit": "price_USD", "rows": report.get("chart", [])[-size:]}

    @app.post("/api/securities/{sid}/prepare", status_code=202)
    def prepare(sid: str):
        report_for(sid)
        return JSONResponse(enqueue(store, sid), status_code=202)

    @app.post("/api/securities/{sid}/refresh", status_code=202)
    def refresh(sid: str):
        report_for(sid)
        return JSONResponse(enqueue(store, sid, "refresh"), status_code=202)

    @app.get("/api/analysis-jobs/{job_id}")
    def job_status(job_id: str):
        job = get_job(store, job_id)
        if not job:
            raise HTTPException(404, "Unknown analysis job")
        return job

    @app.get("/api/company")
    def company():
        report = current_report(store, config)
        return {k: report[k] for k in ("company", "as_of", "expected_session", "data_status", "sources")}

    @app.get("/api/analysis/latest")
    def latest():
        report = current_report(store, config)
        report.pop("chart", None)
        return report

    @app.get("/api/prices")
    def prices(range: Literal["1m", "3m", "1y", "5y", "10y"] = "1y"):
        report = current_report(store, config)
        size = {"1m": 21, "3m": 63, "1y": 253, "5y": 1261, "10y": 2521}[range]
        return {"company": config.identity, "as_of": report["as_of"],
                "data_status": report["data_status"], "sources": report["sources"],
                "unit": "price_USD", "return_convention": report.get("return_convention"),
                "adjustments_verified": report.get("adjustments_verified", False),
                "input_versions": report.get("input_versions", {}),
                "rows": report.get("chart", [])[-size:]}

    @app.get("/api/health")
    def health():
        report = current_report(store, config)
        return {"status": "ok", "data_status": report["data_status"],
                "as_of": report["as_of"], "expected_session": report["expected_session"],
                "sources": report["sources"], "last_job": store.last_job()}
