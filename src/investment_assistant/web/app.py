"""Loopback-only FastAPI application with same-origin mutations and server-side secrets."""

from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import bleach
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from investment_assistant.config import PROJECT_ROOT, Settings, contextual_data_root
from investment_assistant.services.context_artifacts import ContextArtifactStore
from datetime import date
from investment_assistant.domain.assets import asset_profile_for
from investment_assistant.services.news_service import TICKER_PATTERN
from investment_assistant.web.charts import ChartService
from investment_assistant.web.profile import STEPS, UNKNOWN, validate_answers
from investment_assistant.web.store import WorkspaceStore
from investment_assistant.web.workflow import HORIZONS, PRESETS, SECTIONS, Workflow, preferences
from investment_assistant.adapters.investor_profiles import InvestorProfileError
from investment_assistant.adapters.scenario_evidence import LocalScenarioEvidenceSource
from investment_assistant.adapters.scenario_store import ScenarioArtifactStore
from investment_assistant.services.scenario_cli import scenario_cutoff
from investment_assistant.services.scenario_reasoning import ScenarioReasoningService

CATALOG = [("NVDA", "NVIDIA", "Company shares"), ("MSFT", "Microsoft", "Company shares"),
           ("AAPL", "Apple", "Company shares"), ("SPY", "S&P 500 ETF", "Fund"),
           ("BRNT.L", "WisdomTree Brent Crude Oil", "Commodity ETC")]
ASSETS = Path(__file__).parent


def ticker_value(value):
    ticker = value.strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError("Enter a valid investment ticker.")
    return ticker


def render_sections(markdown):
    sections = []
    heading, lines = "Report introduction", []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if lines:
                sections.append((heading, "\n".join(lines)))
            heading, lines = line[3:].strip(), []
        else:
            lines.append(line)
    if lines or not sections:
        sections.append((heading, "\n".join(lines)))
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    tags = set(bleach.sanitizer.ALLOWED_TAGS) | {
        "p", "h1", "h2", "h3", "h4", "hr", "br", "table", "thead", "tbody", "tr", "th", "td"}
    return [{"id": str(i), "title": title, "html": bleach.clean(
        renderer.render(body), tags=tags, attributes={"a": ["href", "title"]},
        protocols={"http", "https", "mailto"})} for i, (title, body) in enumerate(sections)]


def create_app(root: Path = PROJECT_ROOT, *, research_factory=None, reasoner_factory=None,
               chart_service=None, search_provider=None, scenario_source=None) -> FastAPI:
    store = WorkspaceStore(root)
    workflow = Workflow(store, research_factory, reasoner_factory)
    charts = chart_service or ChartService()
    contextual = ContextArtifactStore(contextual_data_root(root))
    scenario_store = ScenarioArtifactStore(contextual.root)
    scenario_source = scenario_source or LocalScenarioEvidenceSource(contextual.root, root / "reports")
    csrf = secrets.token_urlsafe(32)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research")
    store.recover_jobs()
    store.import_reports()

    @asynccontextmanager
    async def lifespan(app):
        try:
            with app.state.company_store.session():
                yield
        finally:
            app.state.company_directory.close()
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Research & Learning", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.workflow, app.state.csrf = store, workflow, csrf
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def privacy(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            same_origin = not origin or origin == f"{request.url.scheme}://{request.headers.get('host')}"
            if (not same_origin or request.headers.get("x-csrf-token") != csrf
                    or request.cookies.get("workspace_csrf") != csrf):
                return JSONResponse({"detail": "Reload the workspace and try again."}, status_code=403)
            if int(request.headers.get("content-length", "0")) > 65536:
                return JSONResponse({"detail": "Request is too large."}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        response.set_cookie("workspace_csrf", csrf, httponly=True, samesite="strict")
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": "Invalid input. Check your choices and try again."}, status_code=422)

    @app.exception_handler(InvestorProfileError)
    async def invalid_profile(request, exc):
        return JSONResponse({"detail": "The local ticker profile is unreadable. Check its format before continuing."}, status_code=422)

    templates = Jinja2Templates(directory=str(ASSETS / "templates"))
    app.mount("/static", StaticFiles(directory=str(ASSETS / "static")), name="static")

    def page(request, name, **values):
        shared = store.get("questionnaire", {})
        return templates.TemplateResponse(request=request, name=name, context={
            "csrf": csrf, "shared": shared, "presets": PRESETS, "sections": SECTIONS,
            "horizons": HORIZONS, "catalog": CATALOG, **values})

    from investment_assistant.single_company.web import mount_company
    # Reuse the existing app, templates, security middleware and local deployment.
    Settings.from_environment()
    mount_company(app, root, page)

    def gate():
        if not store.get("questionnaire", {}).get("completed"):
            raise HTTPException(428, "Complete or skip the short introduction first.")

    async def body(request):
        try:
            value = await request.json()
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Send valid JSON.") from None
        if not isinstance(value, dict):
            raise HTTPException(422, "Send a JSON object.")
        return value

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        if not store.get("questionnaire", {}).get("completed"):
            return RedirectResponse("/onboarding", status_code=303)
        return page(request, "workspace.html", ticker=None, tab="overview",
                    recent=store.get("recent", []))

    @app.get("/onboarding", response_class=HTMLResponse)
    def onboarding(request: Request):
        return page(request, "onboarding.html", steps=STEPS, unknown=UNKNOWN)

    @app.post("/api/onboarding")
    async def onboarding_save(request: Request):
        value = await body(request)
        action = value.get("action", "save")
        if action not in {"save", "complete", "skip"}:
            raise ValueError("Invalid action.")
        answers = validate_answers(value.get("answers", {}))
        step = value.get("step", 0)
        if not isinstance(step, int) or step not in range(6):
            raise ValueError("Invalid step.")
        current = store.get("questionnaire", {})
        state = {"answers": answers, "step": step,
                 "completed": action in {"complete", "skip"} or current.get("completed", False),
                 "personalise": action != "skip" and value.get("personalise", True) is True}
        store.set("questionnaire", state)
        return {"saved": True, "completed": state["completed"]}

    @app.get("/profile", response_class=HTMLResponse)
    def profile_page(request: Request):
        if not store.get("questionnaire", {}).get("completed"):
            return RedirectResponse("/onboarding", status_code=303)
        return page(request, "profile.html")

    @app.get("/api/profile/{ticker}")
    def profile_get(ticker: str):
        gate()
        ticker = ticker_value(ticker)
        return {"explicit": workflow.profiles.load(ticker).model_dump(mode="json"),
                "effective": workflow.context(ticker)}

    @app.post("/api/profile/{ticker}")
    async def profile_save(ticker: str, request: Request):
        gate()
        ticker = ticker_value(ticker)
        try:
            profile, _ = workflow.profiles.update(ticker, await body(request))
        except Exception:
            raise HTTPException(422, "Check the profile fields and value formats.") from None
        return {"saved": True, "configured_fields": list(profile.configured_fields)}

    @app.get("/research/{ticker}", response_class=HTMLResponse)
    def research_page(ticker: str, request: Request, tab: str = "overview"):
        if not store.get("questionnaire", {}).get("completed"):
            return RedirectResponse("/onboarding", status_code=303)
        ticker = ticker_value(ticker)
        if tab not in {"overview", "report", "evidence", "history"}:
            raise HTTPException(404)
        recent = store.get("recent", [])
        store.set("recent", [ticker] + [t for t in recent if t != ticker][:7])
        identity = store.get(f"latest:{ticker}")
        preparation = store.read("preparations", identity) if identity else None
        jobs = [j for j in store.all("jobs") if j["ticker"] == ticker]
        current_job = next((j for j in jobs if preparation and
                            j["fingerprint"] == preparation["fingerprint"]), None)
        outlook = scenario_store.latest(ticker, scenario_cutoff()) if tab == "overview" else None
        return page(request, "workspace.html", ticker=ticker, asset=asset_profile_for(ticker),
                    display_name=next((name for symbol, name, _ in CATALOG if symbol == ticker),
                                      asset_profile_for(ticker).name),
                    tab=tab, recent=store.get("recent", []), preparation=preparation,
                    stale=workflow.is_stale(preparation) if preparation else False,
                    reports=[r for r in store.all("reports") if r["ticker"] == ticker],
                    jobs=jobs, current_job=current_job,
                    context=workflow.context(ticker),
                    outlook=outlook.beginner_view() if outlook else None,
                    market_environment=contextual.read_context(ticker, date.today(),
                                                               include_cases=True))

    @app.get("/api/market-context/{ticker}")
    def market_context_get(ticker: str):
        gate()
        return contextual.read_context(ticker_value(ticker), date.today(), include_cases=True)

    @app.get("/api/scenarios/{ticker}")
    def scenarios_get(ticker: str):
        gate()
        result = scenario_store.latest(ticker_value(ticker), scenario_cutoff())
        return result.beginner_view() if result else None

    @app.post("/api/scenarios/{ticker}")
    async def scenarios_build(ticker: str, request: Request):
        gate()
        ticker = ticker_value(ticker)
        if await body(request):
            raise HTTPException(422, "Market scenarios do not accept personal settings.")

        def build():
            evidence = scenario_source.load(ticker, scenario_cutoff())
            result = ScenarioReasoningService().generate(evidence)
            scenario_store.save(result)
            return result.beginner_view()

        return await run_in_threadpool(build)

    @app.get("/api/search")
    def search(q: str = ""):
        gate()
        query = q.strip().lower()[:80]
        results = [{"ticker": t, "name": n, "type": kind} for t, n, kind in CATALOG
                   if query in f"{t} {n}".lower()]
        if query and not results and len(query) > 1:
            try:
                import yfinance as yf
                quotes = (search_provider(query) if search_provider else yf.Search(
                    query, max_results=8, news_count=0, lists_count=0, recommended=0,
                    include_cb=False, timeout=5).quotes)
                results = [{"ticker": str(item["symbol"]).upper(),
                            "name": str(item.get("shortname") or item.get("longname") or item["symbol"]),
                            "type": str(item.get("quoteType", "Investment"))} for item in quotes
                           if TICKER_PATTERN.fullmatch(str(item.get("symbol", "")).upper())]
            except Exception:
                results = []
        if query and (not results or q.strip().isupper()) and TICKER_PATTERN.fullmatch(query.upper()) and not any(
                r["ticker"] == query.upper() for r in results):
            results.append({"ticker": query.upper(), "name": "Explore this ticker", "type": "Custom ticker"})
        return results

    @app.get("/api/charts/{ticker}")
    def chart_data(ticker: str, window: str = "1M"):
        gate()
        ticker = ticker_value(ticker)
        if window not in {"1M", "3M", "1Y"}:
            raise ValueError("Invalid window.")
        try:
            return charts.get(ticker, window)
        except Exception:
            raise HTTPException(503, "Market data is unavailable. Try again later or choose another investment.") from None

    @app.post("/api/prepare/{ticker}")
    async def prepare(ticker: str, request: Request):
        gate()
        ticker = ticker_value(ticker)
        prefs = preferences(await body(request))
        try:
            result = await run_in_threadpool(workflow.prepare, ticker, prefs)
        except Exception:
            raise HTTPException(503, "Evidence could not be prepared. Check data availability and try again. No paid request was made.") from None
        return {"id": result["id"], "url": f"/research/{ticker}?tab=evidence"}

    @app.get("/api/financial/{ticker}")
    def financial(ticker: str):
        gate()
        try:
            return jsonable_encoder(charts.financial(ticker_value(ticker)))
        except Exception:
            raise HTTPException(503, "Financial comparison data is unavailable. No values were estimated.") from None

    @app.post("/api/generate/{identity}")
    async def generate(identity: str, request: Request):
        gate()
        value = await body(request)
        preparation = store.read("preparations", identity)
        if not preparation:
            raise HTTPException(404, "Evidence preparation not found.")
        if value.get("confirm_paid") is not True:
            raise HTTPException(422, "Confirm the paid request and context preview first.")
        if workflow.is_stale(preparation):
            raise HTTPException(409, "Evidence is outdated. Prepare again after context changes.")
        configured = Settings.from_environment()
        if not configured.openai_api_key and not workflow.reasoner_factory:
            raise HTTPException(422, "Configure OPENAI_API_KEY server-side before generating.")
        if configured.openai_model != preparation["model"]:
            raise HTTPException(409, "Model setting changed. Prepare evidence again to review the request.")
        job, created = store.claim(preparation)
        if created:
            executor.submit(workflow.run, job)
        return {"job": job, "created": created}

    @app.get("/api/jobs/{identity}")
    def job_get(identity: str):
        gate()
        job = store.read("jobs", identity)
        if not job:
            raise HTTPException(404)
        return job

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        store.import_reports()
        return page(request, "history.html", reports=store.all("reports"))

    @app.get("/reports/{identity}", response_class=HTMLResponse)
    def report_page(identity: str, request: Request):
        report = store.read("reports", identity)
        if not report:
            raise HTTPException(404)
        return page(request, "report.html", report=report,
                    report_sections=render_sections(report["markdown"]),
                    notes={n["section"]: n for n in store.all("notes") if n["report_id"] == identity})

    @app.post("/api/reports/{identity}/preferences")
    async def report_preferences(identity: str, request: Request):
        report = store.read("reports", identity)
        if not report:
            raise HTTPException(404)
        report["preferences"] = preferences(await body(request))
        store.put("reports", report)
        return {"saved": True, "paid_request": False}

    @app.post("/api/reports/{identity}/notes")
    async def report_notes(identity: str, request: Request):
        report = store.read("reports", identity)
        if not report:
            raise HTTPException(404)
        value = await body(request)
        section = value.get("section", "report")
        allowed = {s["id"] for s in render_sections(report["markdown"])} | {"report"}
        if section not in allowed or not isinstance(value.get("text"), str) or len(value["text"]) > 8000:
            raise ValueError("Invalid note.")
        note = store.save_note(identity, section, value["text"], value.get("included") is True)
        return {"saved": True, "note": note}

    return app
