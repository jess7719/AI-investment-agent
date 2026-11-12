# Stored single-company analysis

The additive [multi-company browsing layer](multi-company-analysis.md) now extends
this module at `/company`. The formulas, original pilot CLI and forecast gates below
remain the baseline. The directory distinguishes searchable identities, stored
reports and supported financial templates.

The attached 6 November 2026 plan is integrated into the original Python application.
The pilot is **NVDA / NASDAQ / USD / CIK 0001045810**, with **SPY** and **XLK**
benchmarks and a **20-session** research forecast horizon. Existing reports, ticker
exploration, profiles, scenarios and historical context remain available. This module
has its own price lineage; it never mixes Twelve Data prices with existing Yahoo data.

## Run locally

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[web,company,dev]"
.\.venv\Scripts\python.exe -m investment_assistant company status
.\.venv\Scripts\python.exe -m investment_assistant company ingest --full
.\.venv\Scripts\python.exe -m investment_assistant web
```

Open [Company analysis](http://localhost:8000/company), also linked from the main
navigation. The page works before onboarding. Empty and stale states are supported;
there are no fabricated prices or example figures in the real dashboard.

Copy the new fields from `.env.example` into your private `.env`. Supply
`TWELVE_DATA_API_KEY`, `FRED_API_KEY` and a descriptive `SEC_USER_AGENT` with a real
contact email. `COMPANY_DATA_USAGE_RECORD` must describe your reviewed permissions
for private display, normalized storage, derived metrics, modelling and the FRED
series. `COMPANY_RETAIN_RAW=false` is the default; enabling it requires permitted
raw retention. Raw hashes and safe request metadata are always retained.

`COMPANY_ADJUSTMENT_RECORD` records checks of splits **and** dividends for the company
and both benchmarks, currency/exchange identity, actual history coverage and your
account's adjustment entitlement. Requesting `adjust=all` is not proof of those
properties. Without the record, displayed returns are explicitly provisional and
forecast evaluation is disabled. Do not fill these records with a placeholder just
to bypass the gates. Provider keys and acceptance results are not supplied by the plan.

## Storage and ingestion

The default directory is `data/single-company/`, excluded from version control.
`company --root PATH ...` selects another persistent directory for CLI runs; the web
app uses its project root's `data/single-company/`. Schema migrations 1 and 2 are
idempotent SQL changes in `single_company/storage.py`.

SQLite WAL stores normalized price bars and immutable price versions, SEC contexts
and accessions, macro observation/vintage ranges, archived reports, request hashes,
job records, evaluation artifacts and predictions. One database lease serializes
ingestion and evaluation (one-hour expiry for crash recovery). Failed datasets do
not replace their successful versions. Price failure preserves the published report.
Successful changes to other datasets can be retained independently.

An initial run requests up to ten years (capped at 5,000 points) for three adjusted histories plus
NVDA's unadjusted history. Daily runs refetch ten sessions; every seven days they
reconcile the full history. Old versions and reports survive corporate-action
corrections. All normalized series must contain complete exchange sessions and a
completed-session tail. Large moves are flagged rather than deleted. No silent
vendor failover is permitted. A known action can be reconciled immediately with
`company ingest --full`.

SEC submissions verify ticker, exchange and CIK on every run. Companyfacts refresh
on setup, new 10-K/10-Q accessions and weekly reconciliation. Earlier submissions
files are linked, rather than presented as fully downloaded history. All five FRED
series are fetched with vintage ranges; missing values remain NULL, and monthly
series retain their actual observation dates. Truncated responses are rejected.
The baseline is four price calls, submissions, five macro calls, plus occasional
companyfacts. Retries are capped at three, total requests at 80/run, Twelve Data at
about six/minute and SEC below one/second. Timeouts, 429, Retry-After and transient
5xx responses are handled. Credential and HTTP-200 quota errors are not retried.
Exception messages and stored request metadata exclude keys and provider bodies.

## Calculations

All metrics use simple daily close returns and matching exchange sessions. Returns
need window+1 closes; 20-session volatility uses sample standard deviation × √252;
252-session beta uses sample covariance / sample market variance; trailing-year
drawdown uses 253 closes; volume compares the latest bar with the preceding 20 bars.
Relative returns are differences in percentage points. Return calculations stay
within one configured provider and adjustment mode. Current valuation uses a
separate unadjusted company close.

SEC mappings are deliberately narrow and never sum aliases. Contexts preserve units,
start/end dates, accession and publication dates. A fact becomes available no earlier
than the next exchange session after acceptance (or filed date when unavailable).
Repeated contexts select the latest disclosure available at the cutoff. Direct
70–105-day quarters are preferred; YTD and Q4 differences require compatible
cumulative statements in the same accession, unit and tag. This conservative rule
will leave some valid quarters unavailable rather than combine incompatible
restatements. TTM requires four contiguous quarters. Debt requires both configured
components at the same date; unsupported short-term borrowing concepts mean net
debt is unavailable. P/E requires positive TTM earnings, shares no older than 120
days and an earnings period no older than 150 days; shares' date is displayed.
Reported share-count changes can include split effects.

## Forecast experiment

```powershell
.\.venv\Scripts\python.exe -m investment_assistant company evaluate
```

The engine requires verified adjustments and at least 1,261 matching price sessions.
It uses six price features, no revised macro or financial inputs. Ridge coefficients
and scaling come solely from training observations. The rolling window is 1,260
sessions with a 252-sample training minimum. Retraining occurs at the first eligible
origin of each month. Training labels must end strictly before the validation origin.

The most recent 252 eligible origins form the final held-out year. An earlier
126-origin tuning segment selects alpha from 0.1, 1, 10 and 100; reported development
results exclude that tuning segment. Development labels are purged before the final
boundary. Monthly refits within the holdout use only then-completed labels, with
fixed hyperparameters. Nominal 80% intervals use completed prior out-of-sample
residuals, never residuals whose target is still in the future.

Reports contain MAE versus zero and training-mean baselines, direction versus the
training majority, interval width/coverage, daily and nonoverlapping sample counts,
and seeded 1,000-replicate block-bootstrap uncertainty. Promotion requires at least
5% MAE improvement over **both** baselines in development and holdout, a positive
lower bootstrap improvement bound, 70–90% empirical interval coverage and at least
ten nonoverlapping origins per period. These are project rules, not promised returns.
The short held-out sample still limits the strength of any conclusion.

Evaluations, scaler/coefficients, training cutoffs and predictions are archived in
SQLite. The evaluation is frozen per company/benchmark/rule/model specification;
reruns return the saved decision even when newer data arrives. A failed holdout must
lead to a separately designed prospective study, not repeated tuning. If a frozen
decision passes, subsequent successful ingestions can publish one immutable forecast
per origin with an archived monthly model. Corrections to the evaluation's historical
price prefix invalidate publication. Stale or partial sources suppress a current
forecast. Returns are research close-to-close targets before costs, not executable
trading returns, guaranteed intervals or calibrated probabilities.

## API

- `GET /api/company`: configured identity, report date and source dates/statuses.
- `GET /api/prices?range=1y`: stored adjusted bars for matching sessions; range is
  restricted to `1m`, `3m`, `1y`, `5y`, `10y`, capped at 2,521 rows.
- `GET /api/analysis/latest`: units, source links, metrics, filings, lineage and
  forecast state; chart rows are excluded.
- `GET /api/health`: application liveness, separate data status and last ingestion job.

GET requests never call providers. The existing local-only host restrictions,
same-origin/CSRF protection, escaping and content-security policy remain in force.
No unauthenticated refresh endpoint or LLM call is added.

## Scheduling, backup and deployment

Have an external scheduler invoke this command every 30 minutes:

```powershell
powershell.exe -NoProfile -WindowStyle Hidden -File "C:\path\to\project\scripts\run_company_job.ps1" -Scheduled
```

The command checks `America/New_York` time, runs after 22:30 or in the next-morning
08:30–09:30 catch-up window, and skips an already successful completed session.
Holiday and half-day handling comes from `exchange-calendars`, with a 30-minute
publication buffer. A failed task exits nonzero; configure your scheduler to alert
on failure. The schedule is provided but is **not registered or running** by default.

```powershell
.\.venv\Scripts\python.exe -m investment_assistant company backup D:\private-backups\company-2026-11-06.sqlite3
```

Backups use SQLite's backup API and verify integrity. Use a new filename; copy it
off the instance, along with any permitted raw files you want to retain. Restore
while the service/job is stopped: create a fresh data directory, copy the backup
as `company.sqlite3`, run `company --root NEW_DIRECTORY status`, then point a test
app at that project root and verify source dates/versions before switching over.
The offline test restores and reads a real backup.

The shipped server remains local. A remote private deployment still needs an
authenticated HTTPS reverse proxy, explicit trusted-host/same-origin configuration,
secure cookies, one service instance and persistent local disk. Protect the `.env`
and data directory and schedule the same CLI outside the web worker. No remote
deployment target or proxy configuration has been supplied, so those deployment
changes have not been applied. Public redistribution also needs separately
confirmed rights. This integration does not provision hosting or claim five
successful scheduled trading days without observing them. Ephemeral serverless
storage is unsuitable.

## Verification and remaining live acceptance

Run `python tests/run_offline.py -q --basetemp test-output-company-offline`. Fixtures
cover holiday/early-close sessions, strict prices and quota errors, idempotence,
historic price revisions, backup restoration, YTD/Q4 conversion, publication cutoffs,
vintages, missing fundamentals, stale states, routes, forecast purging and scaling.

Before relying on live outputs: record provider permissions, make one small
authenticated contract/coverage check per provider, verify known split/dividend
cases against independent official action records, bootstrap the real history,
inspect mapped financial quarters, freeze the forecast decision, register the
scheduler, observe five consecutive trading-day successes and simulate one outage
and recovery. These require user account entitlements, real keys and elapsed time.

Provider references: [Twelve Data API](https://twelvedata.com/docs),
[SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[FRED observations and vintages](https://fred.stlouisfed.org/docs/api/fred/series_observations.html),
[exchange-calendars releases](https://github.com/gerrymanoim/exchange_calendars/releases).
