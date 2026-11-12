# Historical Context Engine implementation report

The Historical Context Engine converts existing analogue matches into sourced historical cases without changing similarity weights, dates, scores, ranking, or the analogue algorithm. It uses deterministic rules and reviewed public metadata. No ML, predictive probabilities, personalised advice, or OpenAI requests were added or used.

## Architecture

`MarketRegimeService → HistoricalAnalogueService → HistoricalContextService`

The first two services remain responsible for observations and mathematical comparisons. `HistoricalContextService` handles historical metadata filtering, deduplication, event grouping, conditional mechanism chains, historical lessons, and current comparisons. Forward returns enter only after narrative and evidence assembly. Historical news retrieval is outside `HistoricalAnalogueService`.

`HistoricalMetadataProvider` is the metadata-only port. `PublicHistoricalMetadataAdapter` reuses the existing Google News RSS transport/parser, with explicit historical date bounds. It also reads a small reviewed public-source catalogue. It never opens destination news articles, subscriber pages, or login endpoints. RSS items without reviewed summaries remain linked metadata and cannot generate factual event narratives or causal mechanisms. Unsupported dates explicitly report that verified event context is unavailable.

`HistoricalMarketCase` preserves FACT, INTERPRETATION, HISTORICAL OUTCOME, COMPARISON, and LESSON statements. Source references and possible mechanism steps are retained. Internal differences, evidence quality/completeness, source classification and count remain available in backend JSON. `beginner_view()` provides an explicit public projection.

Current comparisons reuse the saved current market snapshot. Optional reviewed current event metadata can support an event comparison only when its category, specific entities, meaningful content and publication window agree. Without that evidence, comparisons use known regime similarities only. No current narrative is invented.

## Sources and retrieval limits

Reviewed examples use original BEA and BLS economic releases, a White House automobile-tariff announcement, and Associated Press market reports. Exact links appear with each case below. Selection prefers official policy/regulator sources, official data, Reuters/AP wires, major financial reporting, then aggregation. Lower-quality metadata cannot displace a stronger source for a duplicate report.

The catalogue contains short original summaries, publication metadata, entities, categories, URLs and structured mechanisms. No article body is stored. Public RSS supplies additional headline metadata for other dates; historical search coverage is incomplete and not guaranteed. Unreviewed RSS items are not promoted to verified historical explanations. Adding reviewed evidence is necessary to deepen unsupported episodes. The engine remains useful offline and does not claim a comprehensive historical news archive.

Date-only reviewed sources have `publication_precision: date`; the stored timestamp is a conservative end-of-day UTC availability bound, and the UI displays only the date. Exact official release timestamps are retained where verified. This version uses calendar-date cases, including same-day releases, rather than claiming to reconstruct a particular intraday trading decision.

## Historical windows and look-ahead controls

The default context window is `[T − 7 calendar days at 00:00 UTC, T at 23:59:59.999999 UTC]`. A configurable 0–31-day lookback is validated. Provider results are independently filtered by timezone-aware publication time. Sources without valid publication timestamps are rejected. Cached context is revalidated against the same window, market, evidence version and requested date.

Future publications cannot enter event selection, clusters, mechanisms, lessons, comparisons, or internal evidence quality. Historical narratives use only dated metadata and the historical regime snapshot. Current evidence has its own current-date window. Future outcomes remain in a separate `historical_outcomes` object. Tests change later returns radically and prove that all non-outcome fields stay identical. The scoring function is patched to fail in a test proving case construction never calls it.

## Event taxonomy and clustering

Supported categories: monetary_policy, interest_rates, inflation, employment, economic_growth, tariffs_trade, geopolitics, earnings, technology, credit_stress, banking, energy, commodity_shock, fiscal_policy, regulation, market_structure, sector_rotation, other.

Metadata classification uses fixed keyword rules; reviewed catalogue entries carry reviewed categories. Canonical URLs, normalized headlines and high token-overlap syndicated headlines are deduplicated after source ordering. Reports are grouped by structured event category, with up to five meaningful developments and up to five linked sources per group. Official-source priority, reviewed summary availability and corroboration determine presentation order; forward outcomes never enter this process. Fewer than two clusters are allowed when the evidence cannot support more, rather than manufacturing developments.

Facts use short reviewed source summaries. Interpretations carry `certainty: possible` and explicit conditional transmission steps. Reviewed directional scenarios distinguish weak spending, continuing growth, technology expectations and energy inflation. Lessons follow these mechanisms; they are never selected from the sign of future returns.

## 20- and 60-session outcomes

The engine reuses the existing `forward_outcomes` implementation. The benchmark's actual observed trading sessions define the windows. A 20-session return requires the analogue-date close plus 20 future benchmark closes; a 60-session return requires 61 closes. Endpoint return is `100 × (end / start − 1)`. Maximum drawdown is the worst percentage decline from the running peak, including the starting close. Asset returns require every exact benchmark session, and excess return is asset return minus benchmark return. Missing or unavailable windows remain null with explicit sufficient-data flags; no date substitution or extrapolation occurs.

Case JSON retains start/end dates, asset and benchmark returns, excess return, and asset/benchmark maximum drawdown. The cards display only 20 and 60 trading days. In `market_regime_only` mode, asset/excess returns are suppressed and the card explicitly says this is a US-market case rather than an SPCX historical case. SPY is labelled “S&P 500 (SPY price proxy)”; outcomes are closing-price returns excluding dividends, not total returns.

The three example outcomes were recalculated from the original saved benchmark observations and drawdown paths. The saved 2 November 2026 SPCX search was reused; market observations were not refreshed or re-ranked.

## Caching and persistence

Reusable context: `data/research/historical_cases/YYYY-MM-DD.json`. The envelope carries US market, evidence version, window, date and evidence-only fields. Cache reuse validates all those fields. Shared context contains no current ticker, current comparison or forward returns. Atomic temporary-file replacement protects writes. Corrupt, mismatched-version and wrong-window entries rebuild; failed/empty lookups are not cached. `--refresh-context` explicitly refreshes historical evidence.

Current comparisons: `data/research/historical_case_comparisons/CURRENT-AS-OF/TICKER/HISTORICAL-DATE-BENCHMARK.json`. This includes the full historical case, current comparison and separated outcomes. The example script also writes portable JSON and readable text under `docs/examples/historical-cases/`.

## Beginner interface and CLI

Every existing analogue expands through “Read historical case”, showing only: WHAT HAPPENED? WHAT IS SIMILAR TO TODAY? WHAT HAPPENED NEXT? Sources. Mechanisms and lessons remain in full backend JSON and are omitted from the public projection. Unavailable 60-session windows remain in backend JSON and are omitted from the beginner card. The rest of the web interface is unchanged. Confidence labels, internal source ranking, internal differences, and dedicated differences sections are excluded from both rendered cards and their public case JSON. Market-regime confidence labels are also removed from this panel and CLI output.

Web GETs are read-only and use existing cache/catalogue data; they never trigger news retrieval or OpenAI calls. The full analogue-scoring artifacts and backend compatibility helpers remain available internally, but legacy feature-only cards are no longer the beginner presentation.

```powershell
investment-assistant analogue-case SPCX 2025-02-28
investment-assistant analogues SPCX --with-context
investment-assistant analogue-case SPCX 2025-02-28 --refresh-context
```

The single-case command reuses saved analogue evidence when available. If the explicitly requested date is not in that saved top set, it computes that date with the existing fixed similarity function and historical feature builder; it does not alter or rerank the saved matches. Default `analogues` output stays concise. `--with-context` prints shortened cases and saves each full structured case. Full backend JSON deliberately retains internal validation fields; terminal and web projections hide them.

## Files added

- `src/investment_assistant/domain/historical_context.py` — typed evidence, context, outcomes, case, chronology validators and public projection.
- `src/investment_assistant/ports/historical_context.py` — metadata provider protocol.
- `src/investment_assistant/adapters/news/historical_metadata.py` — bounded public RSS and reviewed catalogue adapter.
- `src/investment_assistant/adapters/news/historical_sources.json` — reviewed public metadata for the three validation episodes.
- `src/investment_assistant/services/historical_context.py` — independent context engine, clustering, comparisons and cache.
- `src/investment_assistant/services/historical_case_renderer.py` — full and shortened terminal presentation.
- `tests/test_historical_context.py` — offline chronology, evidence, outcome, cache, command and web checks.
- `scripts/build_historical_case_examples.py` — offline reproducible example generation with outcome audits.
- `docs/examples/historical-cases/{2025-02-28,2026-03-03,2025-04-02}.{json,txt}` — complete example artifacts.
- This implementation report and generated shared/comparison cache JSON.

## Files modified

- `src/investment_assistant/cli.py` — new case command and context flags.
- `src/investment_assistant/services/context_cli.py` — concise default, full/short context rendering, saved-case composition.
- `src/investment_assistant/services/context_artifacts.py` — public context projection and read-only card assembly.
- `src/investment_assistant/web/templates/market_context.html` — requested expandable case sections and linked sources.
- `README.md` and `README.en.md` — command examples and links to this report.
- `tests/test_analogue_cases.py` and `tests/test_context_integration.py` — presentation assertions updated for the new requirements; original similarity/backend compatibility tests retained.

## Validation

`pytest` completed through the repository's offline guard: **263 passed**, with two existing dependency warnings (AnyIO assertion rewriting and Starlette/httpx deprecation). Command: `.venv/Scripts/python.exe tests/run_offline.py -q --basetemp test-output-historical-release`.

The 21 new checks cover inclusive historical windows, timezone handling, future-article exclusion, 20/60-session returns and drawdowns, missing windows/sessions, clustering/deduplication/source order, fact/interpretation/mechanism structure, return-independent narratives, current similarities, internal/public separation, market-only labels, shared caching and refresh, corrupt/empty cache behavior, RSS-only access, CLI dispatch and explicit-date lookup, web escaping and read-only GETs. The full suite also retains the original similarity/ranking tests.

`ruff check src tests` and the broader `ruff check src tests scripts` both passed with **All checks passed!** The real single-case CLI was exercised against the saved SPCX case. The example-generation script verified every saved 20/60-session return and benchmark maximum drawdown against its underlying observations.

Windows sandbox loopback restrictions initially prevented FastAPI's in-process test client from starting. The final suite ran with local loopback available while the offline guard continued to reject external connections, live market history and OpenAI requests.

All new tests use fixture providers or metadata-only fixture responses. The offline suite blocks external sockets, live market-data calls and OpenAI Responses/Chat Completions calls. Public sources were checked separately during implementation. No OpenAI API request, paid or otherwise, occurred. There is no model training, forecast probability calculation, future-return ranking, personalised action advice or full-web redesign.

## Full validation cases

These cases compare with the saved current snapshot dated 2 November 2026. They are historical context, not forecasts. The adjacent JSON files retain complete internal reasoning and audit data.

### 2025-02-28

```text
Historical Case — 28 February 2025
Similarity to today: 93.7 / 100
Comparison snapshot as of: 2026-11-02
This is a historical US-market case, not a historical SPCX case.

WHAT HAPPENED?
January consumer spending declined 0.2% in nominal terms while the PCE price index rose 0.3% over the month. This release gave investors both a spending signal and an inflation update.
Shares weakened as concerns about tariffs and inflation weighed on consumers. Treasury yields also fell as demand for safer assets increased.
Nvidia fell 8.5% after its results as enthusiasm for artificial-intelligence shares weakened. The 10-year Treasury yield edged higher that day, showing that daily bond moves differed within the broader window.
Broad equities were in a downtrend. Treasury yields were falling. Investors were reducing exposure to risky assets. Market price swings were within their usual range.

WHAT IS SIMILAR TO TODAY?
- Broad equities were in a downtrend in both periods.
- Treasury yields were falling in both periods.
- Investors were reducing exposure to risky assets in both periods.

WHAT HAPPENED NEXT?
S&P 500 (SPY price proxy) — 20 trading days: -6.48%
Benchmark maximum drawdown: -7.20%; 2025-02-28 to 2025-03-28
S&P 500 (SPY price proxy) — 60 trading days: -0.51%
Benchmark maximum drawdown: -16.44%; 2025-02-28 to 2025-05-27
Returns use closing prices, exclude dividends and are not total returns.

Sources:
[bea-20250228] US Bureau of Economic Analysis — Personal Income and Outlays, January 2025 (2025-02-28T13:30:00+00:00)
https://www.bea.gov/news/2025/personal-income-and-outlays-january-2025
[ap-20250225] Associated Press — How major US stock indexes fared Tuesday, 2/25/2025 (2025-02-25)
https://apnews.com/article/662baaa23f22ff6e74b8a35dd716e3bc
[ap-20250227] Associated Press — Wall Street falls sharply as Nvidia tumbles 8.5% and AI mania falters (2025-02-27)
https://apnews.com/article/91a42e60248e389f1650bd744e6d9a2e

Important: This is historical context, not a forecast.
```

### 2026-03-03

```text
Historical Case — 03 March 2026
Similarity to today: 89.8 / 100
Comparison snapshot as of: 2026-11-02
This is a historical US-market case, not a historical SPCX case.

WHAT HAPPENED?
US producer prices rose 0.5% in January and 2.9% over the preceding year. The release showed that price pressure remained relevant when investors assessed the interest-rate outlook.
The widening war with Iran pushed oil prices higher and shares lower. Treasury yields rose amid inflation concerns, illustrating pressure on stocks and bonds at the same time.
Broad equities were in a downtrend. Treasury yields were falling. Investors were reducing exposure to risky assets. Market price swings were within their usual range.

WHAT IS SIMILAR TO TODAY?
- Broad equities were in a downtrend in both periods.
- Treasury yields were falling in both periods.
- Investors were reducing exposure to risky assets in both periods.

WHAT HAPPENED NEXT?
S&P 500 (SPY price proxy) — 20 trading days: -4.41%
Benchmark maximum drawdown: -7.76%; 2026-03-03 to 2026-03-31
S&P 500 (SPY price proxy) — 60 trading days: +10.92%
Benchmark maximum drawdown: -7.76%; 2026-03-03 to 2026-05-28
Returns use closing prices, exclude dividends and are not total returns.

Sources:
[bls-20260227] US Bureau of Labor Statistics — Producer Price Indexes — January 2026 (2026-02-27T13:30:00+00:00)
https://www.bls.gov/news.release/archives/ppi_02272026.htm
[ap-20260303] Associated Press — Dow drops 400 after trimming an early plunge of 1,200 as oil prices climb even higher (2026-03-03)
https://apnews.com/article/75edbda5b8fa3038b47f143cc16855f0

Important: This is historical context, not a forecast.
```

### 2025-04-02

```text
Historical Case — 02 April 2025
Similarity to today: 86.9 / 100
Comparison snapshot as of: 2026-11-02
This is a historical US-market case, not a historical SPCX case.

WHAT HAPPENED?
The administration announced a 25% tariff on imported cars and certain parts. The car tariff was scheduled to begin on 3 April; businesses with international supply chains faced a change in future import costs.
The published estimate put fourth-quarter real GDP growth at a 2.4% annual rate, slower than the third quarter. It described a still-expanding economy before the coming trade-policy changes.
February PCE prices rose 0.3% over the month; excluding food and energy, prices rose 0.4% and were 2.8% above a year earlier. Investors had to weigh continuing inflation pressure alongside growth and trade-policy risks.
Broad equities were in a downtrend. Treasury yields were falling. Investors were reducing exposure to risky assets. Market price swings were within their usual range.

WHAT IS SIMILAR TO TODAY?
- Broad equities were in a downtrend in both periods.
- Treasury yields were falling in both periods.
- Investors were reducing exposure to risky assets in both periods.

WHAT HAPPENED NEXT?
S&P 500 (SPY price proxy) — 20 trading days: -1.07%
Benchmark maximum drawdown: -12.05%; 2025-04-02 to 2025-05-01
S&P 500 (SPY price proxy) — 60 trading days: +9.45%
Benchmark maximum drawdown: -12.05%; 2025-04-02 to 2025-06-30
Returns use closing prices, exclude dividends and are not total returns.

Sources:
[wh-20250326] White House — Adjusting Imports of Automobiles and Automobile Parts into the United States (2025-03-26)
https://www.whitehouse.gov/presidential-actions/2025/03/adjusting-imports-of-automobiles-and-autombile-parts-into-the-united-states/
[bea-20250327] US Bureau of Economic Analysis — Gross Domestic Product, Fourth Quarter 2024 — Third Estimate (2025-03-27T12:30:00+00:00)
https://www.bea.gov/news/2025/gross-domestic-product-4th-quarter-and-year-2024-third-estimate-gdp-industry-and
[bea-20250328] US Bureau of Economic Analysis — Personal Income and Outlays, February 2025 (2025-03-28T12:30:00+00:00)
https://www.bea.gov/news/2025/personal-income-and-outlays-february-2025

Important: This is historical context, not a forecast.
```
