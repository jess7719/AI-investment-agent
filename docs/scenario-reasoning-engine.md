# Scenario Reasoning Engine implementation report

The new deterministic Scenario Reasoning Engine combines attributed current observations, structured event clusters, a market-regime snapshot and historical benchmark context into exactly three conditional scenarios at each of two horizons. It produces no future prices, expected-return forecasts, scenario probabilities, personalised actions or ML models. No OpenAI adapter or API request is involved.

## Architecture

`domain/scenarios.py` defines strict immutable contracts: `ScenarioEvidencePackage`, `ScenarioEvidenceRef`, `ScenarioEventCluster`, `ScenarioDriver`, `ScenarioTrigger`, `Scenario`, `ScenarioSet`, `HistoricalScenarioSummary` and `ScenarioContradiction`. Personal profiles are absent from this schema; unknown fields are rejected.

`ports/scenarios.py` defines `ScenarioEvidenceSource`. `LocalScenarioEvidenceSource` reads first-party saved inputs, hash-verified immutable research packages, regime snapshots and historical analogue artifacts. It never mines generated AI reports for new facts, reads investor profiles, or uses selected user notes. Historical cases come from the existing context service without remote news access or changes to analogue scoring.

`ScenarioEvidenceBuilder` converts only explicit, allowed research fields. `ScenarioReasoningService` normalises eligibility, calculates historical context, balances factors, creates six scenarios and validates output. All reasoning lives in services. No OpenAI reasoning adapter is introduced. `ScenarioArtifactStore` provides versioned, hash-verified persistence; `scenario_renderer.py` and the public projection provide the simplified terminal/web output.

## Evidence package

The package includes asset identity, current market data, current events, market regime and regime references, historical cases plus separate historical evidence references, fundamentals, filings, event clusters, evidence cutoff, source artifact IDs, exclusions and missing-evidence disclosures.

Every normalized reference records its source, observation/publication timestamp, availability timestamp, evidence type, asset, asset relevance, market/company scope, current/historical status, decision-grade flag, recency eligibility and artifact provenance. Metrics retain values and units; fundamentals also retain reporting period, calculation method and currency. Historical references identify each market case and each available benchmark outcome. Complete original historical cases retain their internal mechanisms, lessons, differences and source metadata.

Market and company evidence use distinct scopes. Historical benchmark evidence always remains market-level, including SPCX market-regime-only cases. Benchmark returns are never substituted for SPCX history or an asset forecast. Raw historical outcome statistics remain internal.

The input normalizer accepts factual source summaries and structured filing-event summaries, not full article bodies. Headline-only news remains review metadata and cannot create an operating claim. Opinions, analyst commentary and user-provided notes are excluded. Missing facts become explicit gaps rather than invented information.

## Time and source controls

All reference timestamps are timezone-aware. Both publication/observation time and `available_at` must be at or before the evidence cutoff. Raw future records are excluded before clustering. Typed inputs with future records are rejected. Known future schedules are permitted only when the scheduling evidence itself was available by the cutoff; a scheduled result is never assumed to be positive.

Daily/date-only inputs are conservatively available at the end of their UTC day. Historical source-window validators remain unchanged. Valuation inputs with a future underlying price date are excluded. Regime references must agree with the actual regime snapshot. A scenario cannot have an evidence cutoff later than its generation timestamp.

Current evidence older than fixed freshness limits is retained for audit but cannot drive scenarios: market data/regimes 7 calendar days, current news 14, filings 90 and fundamentals 180. The UI displays the market snapshot date separately from the evidence cutoff. A stale or incomplete record cannot gain influence by being presented as a new headline.

## Current event clustering

Current events are deduplicated by canonical URL or normalized factual text within the same evidence type, scope, asset and metric. Source priority and decision-grade eligibility select the retained duplicate. Event and filing records are grouped by structured category and scope. Categories cover earnings, monetary policy, AI investment, regulation, geopolitics, company operations, sector rotation, valuation concerns, economic growth, inflation, tariffs/trade and other developments.

Clusters preserve factual summaries, asset relevance, market relevance, evidence refs, timestamps and relevance status. The reasoning service requires eligible cluster membership for event drivers; it does not ingest twenty raw headlines as twenty independent arguments. Multiple records describing the same factor, metric, scope and directional signal cannot multiply that factor's vote.

## Horizon-specific reasoning

These are fixed reasoning priorities, not calibrated probabilities or learned feature weights. They do not change the existing analogue feature weights.

| Factor | 20 trading days | 60 trading days |
|---|---:|---:|
| Market regime | 0.32 | 0.22 |
| Recent momentum | 0.25 | 0.10 |
| Events and scheduled catalysts | 0.28 | 0.18 |
| Fundamentals | 0.08 | 0.40 |
| Historical benchmark context | 0.07 | 0.10 |

The 20-session reasoning emphasises recent market conditions, company momentum, event updates and risk appetite. The 60-session reasoning places more emphasis on business results and macro/sector persistence. Summaries, conditions and invalidation triggers differ by horizon. Known event timing is screened using conservative 35/100-calendar-day relevance bands; this is not a forecast of an exchange holiday calendar or a claim that the event falls on a precise future session.

The Base Case continues the observed balance of conditions; it is not hard-coded to sideways. Bull and Bear Cases are explicitly conditional combinations of improvements or deterioration. No new contract, launch, earnings date or regulatory event is invented. Company catalyst statements require scheduling provenance; favourable or unfavourable outcomes remain hypotheses. Internal causal steps are marked possible, not guaranteed.

The balance averages observations within factors so duplicated coverage cannot dominate. Historical context is capped at its stated allocation even when other evidence is missing. Historical returns alone cannot establish a directional primary scenario. Missing fundamentals cannot amplify the historical allocation.

## Contradictory evidence

Positive and negative references coexist in `contradictory_evidence`. Every scenario retains supportive, restraining and contextual drivers rather than discarding inconvenient observations. The primary scenario balances them. The beginner view marks relevant opposing observations as “Current constraint” and shows a concise selection of reasons. Causal chains, numerical weights, source scores and technical confidence labels remain internal.

## Historical cases and statistics

Both horizons retain sample size, median benchmark return, positive-outcome frequency, worst/best outcome, population standard deviation, interquartile range, case IDs, non-overlapping case IDs, historical support and historical contradiction. Support/contradiction refers to the current broad equity trend; these fields are not scenario probabilities.

History can influence reasoning only with at least 5 cases at similarity 80 or above, at least 5 non-overlapping outcome windows, standard deviation at most 12 percentage points, IQR at most 15 points, and total range at most 40 points. Mixed benchmarks are not pooled. Missing windows are excluded, not extrapolated. Weak evidence disables the historical driver and records why internally. Thresholds are conservative fixed policy rules; they were not fitted or optimised against outcomes.

Existing analogue dates, ranking, similarity scores, outcome calculations, source cutoffs and historical UI sections are unchanged. The scenario engine uses already calculated outcomes only as explicitly labelled market context.

## Validation

Pydantic contracts reject duplicate reference IDs, missing source references, wrong evidence sections, wrong company identities, scope mismatches, future evidence, invalid schedule claims and malformed source URLs. API secrets and credential-bearing URLs are rejected. The normalizer removes duplicate observations and disables stale evidence. Horizon summaries cannot be identical.

Generated scenario prose rejects action words, numeric percentage forecasts, probability language and target-price wording. The deterministic output validator regenerates the expected reasoning from the embedded evidence and compares it with the candidate artifact. This prevents an artifact or caller from quietly adding an unsupported fact, invented catalyst, contrary summary or altered driver. A later free-form AI adapter will require its own semantic claim validator; none is enabled now.

Personal information is rejected by the strict scenario schema or ignored by the allowlisted research converter. The web generation endpoint accepts no personal settings. Profiles and user notes cannot change the scenario output.

The full offline pytest suite passed: **299 passed**, with two existing warnings (AnyIO assertion rewriting and Starlette/httpx deprecation). It ran through `tests/run_offline.py`, which blocks external sockets, live market history and both OpenAI Responses and Chat Completions calls. A fresh temporary directory outside OneDrive avoided a transient Windows file lock in an unchanged historical-cache test.

After the final duplicate-summary refinement, all **36 scenario-specific tests passed** again. Coverage includes six-scenario structure, different horizon priorities/conditions, market/company provenance, benchmark-only historical context, historical statistics and sample/dispersion/overlap gates, contradictory evidence, conditional and known catalysts, stale/missing evidence, future publication/availability/valuation dates, source-ref and identity validation, unsupported claims, duplicated evidence, profile isolation, no probabilities or action advice, artifact integrity/versioning, CLI dispatch/dry run, web rendering/CSRF/read-only GETs, secret exclusion and injected free-provider refresh.

`ruff check src tests` and `ruff check src tests scripts` passed with **All checks passed!** The modified JavaScript passed `node --input-type=module --check`. The real `investment-assistant scenarios SPCX --dry-run` command completed successfully using saved evidence; its complete transcript is preserved under `docs/examples/scenarios/SPCX-cli-dry-run.txt`.

No OpenAI API call, paid or otherwise, occurred. No ML was trained, no scenario probabilities were assigned, and no personalised action advice was generated.

## Persistence and future evaluation

Canonical artifact: `data/research/scenarios/YYYY-MM-DD/TICKER.json`. Full prior snapshots remain under the same day's `versions/` directory. The envelope stores the complete ScenarioSet, an evidence SHA-256 and an artifact SHA-256. Loading checks hashes and reproduces the deterministic reasoning before accepting an artifact.

The artifact retains scenario date, asset, 20d/60d scenarios, generation timestamp, evidence cutoff, exact evidence state, regime snapshot ID, historical case IDs, scenario version, historical summary policy and internal drivers. These snapshots can later be matched with realised returns and reasoning evaluations. No outcome evaluation or ML calibration is implemented in this phase.

## CLI and web

```powershell
investment-assistant scenarios SPCX
investment-assistant scenarios SPCX --dry-run
investment-assistant scenarios SPCX --as-of 2026-11-05
investment-assistant scenarios SPCX --evidence-file path/to/scenario-evidence.json
investment-assistant scenarios SPCX --refresh-evidence
```

Default commands use the latest saved evidence available at the cutoff, with explicit gaps. `--dry-run` prints the same six scenarios plus the evidence package, event clusters, regime, historical cases, statistical context, contradictions and per-scenario references. Full structured artifacts are saved for both modes. `--evidence-file` preserves the supplied package's original cutoff. Optional `--refresh-evidence` retrieves free public daily market data, attributed fundamental context and public RSS metadata; it never invokes a paid reasoner or subscriber login. It is restricted to current-date collection.

The existing asset overview has one new OUTLOOK section containing 20- and 60-trading-day sections, each with Base, Bull and Bear Cases, reasons, conditions and invalidation triggers. Build/Refresh uses a same-origin, CSRF-protected local POST and saved first-party evidence. GETs are read-only. The rest of the site and the simplified historical-case cards are unchanged. The public API excludes raw evidence scores, mechanisms, internal priorities and confidence fields.

## Files added

- `src/investment_assistant/domain/scenarios.py`
- `src/investment_assistant/ports/scenarios.py`
- `src/investment_assistant/adapters/scenario_evidence.py`
- `src/investment_assistant/adapters/scenario_store.py`
- `src/investment_assistant/services/scenario_evidence.py`
- `src/investment_assistant/services/scenario_reasoning.py`
- `src/investment_assistant/services/scenario_renderer.py`
- `src/investment_assistant/services/scenario_cli.py`
- `src/investment_assistant/web/templates/outlook.html`
- `tests/test_scenarios.py`
- `scripts/build_scenario_example.py`
- `docs/examples/scenarios/SPCX-outlook.txt`, `SPCX-dry-run.txt`, `SPCX-cli-dry-run.txt`, `SPCX.json`
- This report and generated scenario snapshots under the configured private research root.

## Files modified

- `src/investment_assistant/cli.py` — scenario command and options.
- `src/investment_assistant/web/app.py` — saved outlook view and gated GET/POST routes.
- `src/investment_assistant/web/templates/workspace.html` — includes the Outlook section.
- `src/investment_assistant/web/static/app.js` — Build/Refresh handler.
- `README.md`, `README.en.md` — command examples and documentation link.

## SPCX dry-run example

The validation uses the latest saved first-party inputs available at generation time, without a network or OpenAI call. The market snapshot is dated **2 November 2026**. There are **10** sufficiently similar historical market cases. All historical support remains market-level because the saved SPCX analogue mode is `market_regime_only`. No current company price record, verified company news/filing summary, attributed fundamental metric or scheduled company catalyst was available in the saved input set. Old generated AI reports and undated article-extract caches were deliberately not treated as fresh evidence.

The 20-session historical benchmark summary has 10 independent cases, median return about 0.11 and dispersion about 5.33 percentage points. The 60-session summary has 10 cases, 8 non-overlapping windows, median return about 6.74 and dispersion about 6.94 points. Those are historical benchmark statistics, not expected SPCX returns. The distribution contains both supporting and contradictory cases, and the historical contribution stays capped.

The current drivers cite `regime/equity_trend`, `regime/risk_sentiment`, `regime/rates` and `regime/volatility`. Historical drivers cite each eligible `history-outcome/HISTORICAL-DATE/SPY/SPCX/20d` or `/60d` reference, with the benchmark source, outcome-end timestamp and original historical case retained. The full internal dry run lists every driver and supporting reference for all six scenarios. No fabricated company-history record is present.

```text
Outlook for SPCX
Evidence cutoff: 2026-11-06T17:22:25.249756+00:00
Market snapshot: 2026-11-02

20-DAY OUTLOOK

Base case
The current evidence supports a cautious primary scenario for SPCX over twenty trading days if recent market and price conditions continue. Company-specific direction remains unresolved because recent asset evidence is incomplete.
Why?
- Broad equities are in a downtrend.
- Market sentiment is risk-off.
- Current constraint: Comparable historical benchmark cases had mixed outcomes; they provide market context, not a company forecast.
What must remain true?
- The broad market environment does not change materially.
- New company information does not materially alter the operating picture.
What would change this view?
- A sustained change in market breadth and risk appetite develops.
- Verified company results materially differ from the current evidence.

Bull case
A stronger near-term outcome for SPCX becomes more plausible if risk appetite improves and company-specific momentum or verified event evidence strengthens.
Why?
- Comparable historical benchmark cases had mixed outcomes; they provide market context, not a company forecast.
- Current constraint: Broad equities are in a downtrend.
- Current constraint: Market sentiment is risk-off.
What would need to happen?
- Market breadth and risk appetite improve.
- Fresh company evidence supports operating progress.
What would change this view?
- Market weakness persists despite attempted recoveries.
- Verified operating evidence fails to improve.

Bear case
A weaker near-term outcome for SPCX becomes more plausible if risk appetite deteriorates and company momentum or new factual evidence weakens.
Why?
- Broad equities are in a downtrend.
- Market sentiment is risk-off.
- Current constraint: Comparable historical benchmark cases had mixed outcomes; they provide market context, not a company forecast.
What would need to happen?
- Broad market pressure persists or becomes more severe.
- Verified company evidence deteriorates or fails to offset market pressure.
What would change this view?
- A sustained market recovery develops.
- Strong verified business progress counters the downside conditions.

60-DAY OUTLOOK

Base case
Over sixty trading days, the primary scenario for SPCX remains cautious, with business performance and the macro environment carrying greater emphasis. Missing decision-grade fundamentals limit the company-specific conclusion.
Why?
- Broad equities are in a downtrend.
- Market sentiment is risk-off.
- Current constraint: Comparable historical benchmark cases had mixed outcomes; they provide market context, not a company forecast.
What must remain true?
- The macro and sector environment remains broadly consistent.
- New business data does not materially alter the operating picture.
What would change this view?
- Macro conditions shift persistently.
- A material change in business growth or financial resilience is verified.

Bull case
A stronger medium-term outcome for SPCX becomes more plausible if verified business progress is sustained and the macro or sector backdrop becomes more supportive.
Why?
- Comparable historical benchmark cases had mixed outcomes; they provide market context, not a company forecast.
- Current constraint: Broad equities are in a downtrend.
- Current constraint: Market sentiment is risk-off.
What would need to happen?
- Financing and sector conditions become more supportive.
- Verified operating results show sustained progress.
What would change this view?
- Macro financing pressure intensifies.
- Later business evidence fails to sustain operating progress.

Bear case
A weaker medium-term outcome for SPCX becomes more plausible if operating progress deteriorates or persistent macro pressure weighs on business expectations.
Why?
- Broad equities are in a downtrend.
- Market sentiment is risk-off.
- Current constraint: Comparable historical benchmark cases had mixed outcomes; they provide market context, not a company forecast.
What would need to happen?
- Prolonged macro pressure affects business demand.
- Verified business results deteriorate.
What would change this view?
- Macro and sector conditions improve persistently.
- Sustained business progress counters the downside conditions.

Evidence gaps:
- Recent company-specific price evidence is unavailable.
- Decision-grade current fundamentals are unavailable.
- No verified upcoming company catalyst is established by the supplied evidence.
- Verified current company news or filing summaries are unavailable.

These are conditional scenarios, not forecasts or personalised investment advice.
No OpenAI request was made.
```

Internal per-scenario evidence references:

| Horizon | Scenario | Current observation references | Historical outcome references |
|---|---|---|---|
| 20d | base | regime/equity_trend, regime/risk_sentiment, regime/rates, regime/volatility | history-outcome/2025-02-28/SPY/SPCX/20d, history-outcome/2026-03-03/SPY/SPCX/20d, history-outcome/2025-04-02/SPY/SPCX/20d, history-outcome/2018-11-16/SPY/SPCX/20d, history-outcome/2023-10-30/SPY/SPCX/20d, history-outcome/2018-03-29/SPY/SPCX/20d, history-outcome/2025-11-21/SPY/SPCX/20d, history-outcome/2019-10-08/SPY/SPCX/20d, history-outcome/2018-10-16/SPY/SPCX/20d, history-outcome/2024-05-01/SPY/SPCX/20d |
| 20d | bull | regime/equity_trend, regime/risk_sentiment, regime/rates, regime/volatility | history-outcome/2025-02-28/SPY/SPCX/20d, history-outcome/2026-03-03/SPY/SPCX/20d, history-outcome/2025-04-02/SPY/SPCX/20d, history-outcome/2018-11-16/SPY/SPCX/20d, history-outcome/2023-10-30/SPY/SPCX/20d, history-outcome/2018-03-29/SPY/SPCX/20d, history-outcome/2025-11-21/SPY/SPCX/20d, history-outcome/2019-10-08/SPY/SPCX/20d, history-outcome/2018-10-16/SPY/SPCX/20d, history-outcome/2024-05-01/SPY/SPCX/20d |
| 20d | bear | regime/equity_trend, regime/risk_sentiment, regime/rates, regime/volatility | history-outcome/2025-02-28/SPY/SPCX/20d, history-outcome/2026-03-03/SPY/SPCX/20d, history-outcome/2025-04-02/SPY/SPCX/20d, history-outcome/2018-11-16/SPY/SPCX/20d, history-outcome/2023-10-30/SPY/SPCX/20d, history-outcome/2018-03-29/SPY/SPCX/20d, history-outcome/2025-11-21/SPY/SPCX/20d, history-outcome/2019-10-08/SPY/SPCX/20d, history-outcome/2018-10-16/SPY/SPCX/20d, history-outcome/2024-05-01/SPY/SPCX/20d |
| 60d | base | regime/equity_trend, regime/risk_sentiment, regime/rates, regime/volatility | history-outcome/2025-02-28/SPY/SPCX/60d, history-outcome/2026-03-03/SPY/SPCX/60d, history-outcome/2025-04-02/SPY/SPCX/60d, history-outcome/2018-11-16/SPY/SPCX/60d, history-outcome/2023-10-30/SPY/SPCX/60d, history-outcome/2018-03-29/SPY/SPCX/60d, history-outcome/2025-11-21/SPY/SPCX/60d, history-outcome/2019-10-08/SPY/SPCX/60d, history-outcome/2018-10-16/SPY/SPCX/60d, history-outcome/2024-05-01/SPY/SPCX/60d |
| 60d | bull | regime/equity_trend, regime/risk_sentiment, regime/rates, regime/volatility | history-outcome/2025-02-28/SPY/SPCX/60d, history-outcome/2026-03-03/SPY/SPCX/60d, history-outcome/2025-04-02/SPY/SPCX/60d, history-outcome/2018-11-16/SPY/SPCX/60d, history-outcome/2023-10-30/SPY/SPCX/60d, history-outcome/2018-03-29/SPY/SPCX/60d, history-outcome/2025-11-21/SPY/SPCX/60d, history-outcome/2019-10-08/SPY/SPCX/60d, history-outcome/2018-10-16/SPY/SPCX/60d, history-outcome/2024-05-01/SPY/SPCX/60d |
| 60d | bear | regime/equity_trend, regime/risk_sentiment, regime/rates, regime/volatility | history-outcome/2025-02-28/SPY/SPCX/60d, history-outcome/2026-03-03/SPY/SPCX/60d, history-outcome/2025-04-02/SPY/SPCX/60d, history-outcome/2018-11-16/SPY/SPCX/60d, history-outcome/2023-10-30/SPY/SPCX/60d, history-outcome/2018-03-29/SPY/SPCX/60d, history-outcome/2025-11-21/SPY/SPCX/60d, history-outcome/2019-10-08/SPY/SPCX/60d, history-outcome/2018-10-16/SPY/SPCX/60d, history-outcome/2024-05-01/SPY/SPCX/60d |

