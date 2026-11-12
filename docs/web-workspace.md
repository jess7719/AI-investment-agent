# Guided research workspace

Install and launch from the project directory in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[web,dev]"
.\.venv\Scripts\investment-assistant.exe web
```

Open <http://localhost:8000>. The server binds to `127.0.0.1` only. An optional
`--port 8002` changes the port. There is no Node build, CDN or external font request.
Stop the server with Ctrl+C. This is a single-user local application, not a hosted service.

The first visit introduces five optional questionnaire steps. Answers and progress
are saved on this computer, including progress through a refresh. Review the editable
summary and choose **Start exploring**, or use **Explore without personalisation**.
Revisit the questionnaire through Profile. Previously saved reports remain available
at **Saved reports** before the questionnaire is completed.

Search a company (the starter catalog is local; other company names use Yahoo Finance
search) or enter a Yahoo Finance ticker. The Overview
shows adjusted daily prices, a comparison rebased on common trading dates, and trading
volume. Every chart includes an explanation, takeaway, sources, units, dates and table.
1M/3M/1Y are calendar windows ending at the latest available trading observation.
Comparisons use each series' own quote currency; they are not currency-converted returns.
Unknown regions show unavailable comparisons rather than an arbitrary US benchmark.
The optional financial context panel uses configured peers (currently NVDA/AMD and
SPCX/RKLB), or product/futures context for the configured Brent commodity ETC. Missing
metrics remain unavailable. Verify reporting periods and official filings before relying
on third-party financial comparisons.

Choose **Beginner Overview**, **News Brief**, or **Deep Analysis** on the Report tab.
Select sections, explanation depth and horizon emphasis, then **Prepare evidence**.
This reuses the CLI's research collector, evidence depth labels, caches and report schemas.
Evidence, risks and limitations cannot be removed. On the Evidence tab, inspect the exact
personal context and selected user commentary before confirming a paid AI request.
All API keys stay in server configuration. No paid request occurs during preparation.

Shared questionnaire answers are stored in `profiles/workspace.sqlite3`, separately from
the existing `profiles/TICKER.json` files. Explicit non-null ticker fields override shared
defaults for matching fields. Clearing a ticker field restores the shared default. Employment,
income and an illustrative decline reaction never set `risk_tolerance`. Risk willingness
and loss capacity are distinct supplied facts, not an inferred score. Unanswered fields stay
unknown. Explanation settings never automatically select or recommend an investment.

Reports are immutable content snapshots in the local workspace database. Their reading
preferences and notes can be edited without another API call. Optional sections may be
hidden; mixed sections containing material evidence, uncertainty or risks remain visible.
Changing the saved reading horizon does not rewrite the original assessment. To get new
analysis, prepare evidence again and review another paid request explicitly.

Each section and whole report has an editable private note, keyed to that report version.
Notes are excluded from subsequent requests unless **Select this note for future analysis**
is checked. Selected notes for the same ticker appear in the preparation preview with a
**User commentary, not verified evidence** label. Changing a profile or selected notes makes
the preparation stale. A private unselected note does not invalidate evidence.

Generation runs in a background worker. Jobs and their request fingerprints are persisted
in SQLite; concurrent clicks and refreshes share one job. Web SDK requests have automatic
retries disabled. A process restart marks queued/running jobs interrupted and never
automatically sends them again. A successful HTTP response is retained in
`reports/web-receipts/` before schema parsing, and deep validation continues to use the
existing pending/rejected artifact pipeline. Ambiguous attempts are retained by the shared
`AnalysisAttemptStore`. Check usage and artifacts before explicitly preparing a changed
request after a failure. The browser does not provide an automatic retry button.

Saved web evidence is hash-verified and immutable under `reports/web-evidence/`. Reports
still use the existing CLI Markdown and structured formats. CLI reports are imported as
version snapshots without modifying their original files. Back up `profiles/`, `reports/`
and `notes/` together to retain the workspace. These directories are Git-ignored. Local
does not mean encrypted: protect this computer and any filesystem synchronization you use.

Personal values are excluded from access logs and exception messages returned by the web
workflow. Mutations require a same-origin CSRF token. HTML from report Markdown is escaped
and sanitized, assets are served locally, and hostnames are restricted. The app is intended
to be run as one server process on loopback.

Offline verification:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=test-output-check
.\.venv\Scripts\python.exe -m ruff check src/investment_assistant/web tests/test_web_workspace.py
.\.venv\Scripts\python.exe tests/serve_web_fixture.py
```

The final command provides an isolated browser test at <http://localhost:8001> using
synthetic charts and a fake report reasoner. Its files are kept under `test-output-browser/`;
it does not contact financial providers or OpenAI. Use it for desktop/mobile, keyboard,
refresh/resume, notes and generation acceptance checks. It is not a live research source.

Portfolio management, trading, courses, chat and hosting remain outside this version.
