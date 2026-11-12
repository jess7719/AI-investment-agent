# Market Regime Engine

The listed-equity engine describes broad-market conditions using reproducible rules.
It does not predict prices, infer Federal Reserve policy from Treasury yields, or
change investment advice. There is no ML model, LLM interpretation, or user-note input.

```powershell
investment-assistant regime
investment-assistant regime --as-of 2024-05-17
investment-assistant regime --benchmark QQQ
```

Dates mean end-of-day evidence available on or before that date. The live adapter
excludes today's possibly unfinished daily candle. A weekend as-of date therefore
normally uses the preceding completed session. Future CLI dates are rejected.

## Architecture and sources

`domain/regime.py` defines validated immutable Pydantic models: `DailyObservation`,
`MacroObservation`, `RegimeEvidence`, `RegimeDimension`, and `MarketRegimeSnapshot`.
`ports/regime.py` has separate `BroadMarketDataProvider`, `VolatilityDataProvider`,
`RatesDataProvider`, and `MacroDataProvider` interfaces. The first adapter implements
the three market capabilities using the existing yfinance dependency; each capability
can be supplied independently to `MarketRegimeService`.

Default market symbols are SPY and QQQ; volatility is ^VIX; the yield proxy is ^TNX
(yield percent, changes measured in percentage points). These are third-party Yahoo
market proxies, not official macro releases. No new external data provider or paid
subscription is added. Provider exceptions become missing data; exception messages
are never saved because they could contain credentials.

The adapter uses daily `Close` with `auto_adjust=False`, `back_adjust=False`, no
actions and explicit date bounds. yfinance's end date is exclusive:
[official history API documentation](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html).
The engine does not use `Adj Close`. Yahoo can still revise or split-normalize
historical records: these data are **not a certified point-in-time vintage archive**.
Raw input windows and retrieval timestamps are saved so each recorded calculation
can be reproduced exactly from its artifact. An archival provider can replace Yahoo
later without changing the classifier.

## Rules and units

All numerical rule thresholds are in `RegimeConfig` in `config.py`. Change them by
injecting a configuration; each snapshot saves the actual configuration used.

| Dimension | First version rule |
| --- | --- |
| Equity trend | Each benchmark contributes signed votes from 5/20/60-session price returns and distance from 20/60-session moving averages. Return deadband is +/-1%; MA deadband +/-0.5%. Mean vote >0.4 gives uptrend, <-0.4 downtrend, otherwise sideways. Strong trends require all available votes to agree and every available 60-session return to cross +/-8%. |
| Volatility | Convert VIX level, its trailing percentile and 20-session realised volatility to ordinal bands; use upper median. Level boundaries: 15/25/35; percentile boundaries: 20/80/95; realised-vol boundaries: 10/20/35%. The four bands are low, normal, elevated, stressed. |
| Rates | Latest yield plus 5/20-session changes. Signed change votes use +/-0.05 and +/-0.15 percentage-point deadbands. Median vote determines falling/stable/rising. Latest level alone cannot classify direction. |
| Monetary policy | Unknown. Official publication-dated vintage port exists; no policy classification rule is enabled in v1. |
| Inflation | Unknown. No official CPI provider or inflation rule is enabled. |
| Growth | Unknown. No official growth provider or growth rule is enabled. Stock returns are never used as growth evidence. |
| Risk sentiment | Equal-weight mean of available SPY/QQQ trend scores and volatility score. Volatility scores low/normal/elevated/stressed = +1/0/-0.5/-1. Scores above +0.25 are risk_on; below -0.25 risk_off; otherwise neutral. Component scores and total are saved. No credit or sector proxies are implicitly assumed. |

Returns are `100*(close[T]/close[T-n]-1)`. MA distance is
`100*(close[T]/mean(last n closes)-1)`. Realised volatility is sample standard
deviation of the last 20 daily simple returns, annualized by `sqrt(252)*100`.
VIX percentile counts observations <= latest over the trailing 252 levels (including
the latest observation), requiring at least 60 observations. Boundaries are inclusive
on the higher volatility band; trend/rate deadbands are strict.

## Confidence, missingness and audit

Confidence is about evidence quality and agreement, not likelihood of profit.
Three or more agreeing votes can have high confidence, two medium. Conflicting
votes, a single available series/proxy, stale observations, partial coverage or
missing component evidence lower confidence to low. No usable decision input means
unknown. Treasury direction remains low-confidence because it uses one yield proxy.
These rules do not claim that correlated indicators are independent evidence.

Staleness defaults to more than five calendar days. Internal gaps longer than seven
calendar days are marked partial; historical similarity excludes stale/partial
features. The classifier may retain a stale classification with low confidence and
visible timestamps. Missing values are null, never zero. Histories are sorted and
deduplicated; conflicting observations for the same date are rejected. A daily
observation must have finite positive value; missing volume stays null.

Every derived input has source, series, observation date, retrieval time, observed
level, derived value, unit, calculation, rule, status, confidence and its exact raw
input window. Snapshots validate date bounds. Official macro vintages, if supplied
through the future port, are filtered by publication date and retained as facts;
their classification remains unknown until a reviewed rule is enabled.

## Storage and integration

Default artifacts: `data/research/regimes/YYYY-MM-DD.json`. Set
`INVESTMENT_RESEARCH_ROOT` to a private directory to relocate contextual artifacts.
Non-SPY benchmark artifacts append `-BENCHMARK` to avoid overwriting SPY artifacts.
Writes replace files atomically; rerunning a date explicitly refreshes that artifact.
Existing immutable evidence packages retain their original embedded snapshot.

The web overview and `GET /api/market-context/TICKER` read saved default-SPY context
only. They never fetch market data or generate AI analysis. They show artifact date,
age and confidence, including an unavailable state before artifacts exist.
Research preparation adds `market_regime` and `historical_analogues` to its evidence
package, labelled deterministic contextual evidence, with an instruction not to
change advice or scenario probabilities in this phase. It reads local artifacts
at or before the research date; it does not refresh them automatically. Corrupt or
future-dated artifacts are skipped. Existing reports and recovery paths are unchanged.

## Limitations and future work

This is a US market-proxy prototype for listed equities. It has no exchange calendar
for detecting every missing session, certified vintage prices, total-return series,
official macro classifications, international benchmark mapping or specialised
commodity/crypto/bond/options rules. Markets may move for reasons absent from these
indicators. Freshness, disagreement and missing coverage must remain visible.

Future ML can consume the same timestamped observations and features behind the
ports. It should be separately versioned, use point-in-time training/validation
splits, retain deterministic baselines and never substitute model output for facts.

## Offline verification

```powershell
python tests/run_offline.py -q --basetemp test-output-offline
python -m ruff check src tests
```

The offline launcher blocks external sockets, live yfinance history and paid OpenAI
request methods. Loopback sockets remain available because Windows asyncio and
Starlette need them for in-process web tests. Some restricted sandboxes require
running that suite with local loopback access enabled. Fixture values are synthetic
test evidence and are never saved into the application's live research directory.
