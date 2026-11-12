# Historical Analogue Engine

This engine asks when a listed equity traded in similar observable market conditions.
It reports **historical forward outcomes**, not forecasts or causal conclusions.
Negative matches stay visible. User notes and LLM output never enter its inputs.

```powershell
investment-assistant analogues NVDA
investment-assistant analogues SPCX --as-of 2026-10-30 --top 10 --benchmark SPY
```

No OpenAI client is constructed by these commands. `--top` accepts 1–100. There is
no new ML dependency or model training. An unsupported configured commodity asset
is rejected. Unknown tickers follow the project's existing equity assumption; the
caller must supply a listed-equity ticker, not a bond, option, crypto or futures code.

## Models and responsibilities

`HistoricalRegimeSnapshot` keeps features and evidence apart from outcomes.
`HistoricalFeatureBuilder` only slices trailing observations and calls the same
regime classifier used for current dates. `HistoricalAnalogueService` selects
consistent feature coverage, scores/ranks candidates, and then calculates outcomes
for selected dates. `HistoricalAnalogue`, `HistoricalForwardOutcome`,
`OutcomeSummary` and `AnalogueSearchResult` retain the result and audit settings.

The result includes the current snapshot, actual normalized weights/scales, excluded
features and reasons, candidate count, selected sample size and warnings. Each match
retains raw feature windows, category penalty, feature distance, coverage and raw
outcome windows. Re-fetching a provider is not needed to reproduce saved values.

## Features, scales and default weights

Percent returns/volatility use percentage units (5 means 5%, not 0.05). Yield levels
and changes use percentage points. Configuration is `AnalogueConfig` in `config.py`.

| Feature | Weight | Fixed distance scale |
| --- | ---: | ---: |
| Broad market 5-session return | 4% | 5 |
| Broad market 20-session return | 8% | 10 |
| Broad market 60-session return | 8% | 20 |
| VIX level | 7.5% | 20 |
| VIX trailing percentile | 7.5% | 100 |
| 10-year yield level | 7.5% | 3 |
| 10-year yield 20-session change | 7.5% | 1 |
| Risk sentiment score | 15% | 2 |
| Target 20-session momentum | 7.5% | 15 |
| Target 60-session momentum | 7.5% | 30 |
| Target 20-session realised volatility | 10% | 50 |
| Target volume / trailing 20-session average | 10% | 2 |

Broad-market numerical features use the chosen primary benchmark; risk sentiment
also uses the other configured market benchmark. Volume average includes the
observation date. Zero average volume produces missing ratio, not division by zero.

Features must exist today and on at least 80% of eligible candidate dates to enter
the search. Features with zero configured weight are excluded. Remaining weights
are renormalized and saved. Candidates require at least 60% weighted feature coverage.
Missing, stale and partial values never silently become zero. Exclusions are explicit.

## Similarity formula

For each included feature `j`, numerical distance is
`d_j = min(abs(current_j - candidate_j) / configured_scale_j, 1)`.
A missing comparison contributes distance 1. With normalized weights `w_j`:

```text
D = sum(w_j * d_j)
P = category_penalty * mismatching_known_categories / comparable_known_categories
similarity = 100 * (1 - min(1, D + P))
```

The default category penalty is 0.10. Categories are equity trend, volatility, rates
and risk sentiment. Unknown categories are not treated as matching evidence and are
excluded from the category denominator (zero comparable categories means no penalty).
Complete identical vectors/categories score 100. Missing-identical vectors do not
earn credit for unknown values. These scores are not calibrated probabilities.

Fixed scales are not fitted to the whole time series, so future observations cannot
affect a historical feature's normalization. There is no recency weighting. Scores
sort descending; equal scores break ties by ascending date. Selected dates are
separated by at least 20 benchmark sessions by default, configurable down to zero.
This reduces duplicate neighbouring matches but does not establish independence:
60-day outcome windows can still overlap. Sample summaries are descriptive.

## Temporal safeguards

1. Histories are normalized and every slice filters both date and `available_on`.
2. All features for date T use closes at/before T. Trailing windows include T.
3. Historical features and forward labels have separate models/functions.
4. Search history defaults to ten calendar years, plus warm-up data. Numerical
   features require complete trailing windows; macro publication vintages cannot
   be drawn from the future.
5. An embargo of `60 + maximum outcome window` (default 120 benchmark sessions)
   excludes recent candidates. Even the longest selected forward outcome ends
   before the current 60-session market feature window starts.
6. Forward calculations only see observations available at the query as-of date.
   Domain validators reject future input/output windows.
7. Changing observations after a historical feature date cannot change that feature;
   changing observations after the query cutoff cannot change the search. Offline
   tests enforce both, even with providers that ignore requested end dates.
8. Similarity never reads returns after the analogue date. Negative outcomes do not
   affect eligibility or ranking. User notes and LLM interpretations are excluded.

Algorithmic cutoff protection does not prove Yahoo's historical data are the exact
vintage available at T. Yahoo can revise/split-normalize records. Production
backtesting needs archival prices, corporate actions, delisted assets and official
release vintages. See [market sources and limitations](market-regime.md).

## Outcomes and new listings

Outcome windows are 1, 5, 20 and 60 **benchmark trading sessions** after T. Price
return is `100*(close[T+h]/close[T]-1)`. Benchmark return uses identical endpoints;
excess return is asset return minus benchmark return. Maximum drawdown tracks the
lowest percentage decline from a running peak, including the initial close, and
is <=0. These are price returns, not dividend-inclusive total returns. Splits,
corporate actions, delistings and vendor revisions can distort comparisons.

Asset outcomes require every benchmark session in the window, including the exact
initial/final dates. Missing endpoints/sessions produce null asset return/excess/
drawdown; benchmark outcomes can remain available. Unmatured windows return null.
No synthetic prices or substituted starting dates are used.

`asset_specific` requires at least 126 target sessions, complete current target
features, and at least five eligible historical candidate dates with the configured
target features. Otherwise the engine explicitly falls back to `market_regime_only`.
In that mode, target features are excluded and **all company-specific forward returns
are unavailable**. Only benchmark outcomes describe historical market behaviour.
Market-only mode can itself be unavailable when broad-market evidence is missing.
It does not invent historical company trades or silently substitute peers.

## Summary and small samples

Each window stores asset sample size, median/mean return, positive-return frequency,
median excess return, worst/best historical outcome; unavailable observations are
excluded individually. Benchmark sample size and median are reported separately.
Market-only mode therefore shows asset n=0 and benchmark n equal to available matches.

| Sample | Warning |
| --- | --- |
| 0–4 | insufficient historical sample |
| 5–9 | very limited evidence |
| 10–29 | limited evidence |
| 30+ | more informative but still not predictive |

The overall sample is the selected match count, not every candidate inspected.
Positive-return frequency is a descriptive fraction of historical observations,
not a probability of future profit. No negative cases are removed from summaries.

## Artifacts, UI and future ML

Default path: `data/research/analogues/YYYY-MM-DD/TICKER.json`; override with
`INVESTMENT_RESEARCH_ROOT`. Non-SPY searches add `-BENCHMARK` to the filename.
Running analogues also saves its current market-regime snapshot. No secrets are saved.
The existing web overview shows every saved top default-SPY match as an expandable
case card, alongside artifact age, mode, sample count and warning. Loading that view
makes no market-data or AI request. Existing saved JSON searches gain the cards on
read, so no live refresh or artifact migration is necessary.

Research evidence packages receive bounded saved context (top three matches plus
all window summaries), clearly labelled deterministic contextual evidence. They
state that comparisons are not forecasts and must not change advice or scenario
probabilities in this phase. Full audit windows stay in the JSON artifacts.

## Deterministic case cards

`services/analogue_cases.py` builds `AnalogueCaseCard` display models from an existing
`AnalogueSearchResult`. The CLI uses the same cards as the web view. Cards retain:

1. Historical date and the original similarity score.
2. All seven historical regime labels and their confidence.
3. Why the date resembles the saved current snapshot, with its as-of date.
4. Known matching and differing regime labels; unknown is never called a match.
5. Main matching and mismatching numerical features, their actual historical/current
   values, scoring weights and distance relative to the saved scale.
6. Explicit unavailable/excluded evidence, including missing macro data and target
   momentum in market-only mode. Missing values never become zero.
7. The configured benchmark's 5/20-session price returns and maximum drawdown for
   each of those windows. Default benchmark labels are SPY; other benchmarks keep
   their own names. Drawdown is measured from a running peak using daily closes;
   these windows and values are unchanged from the existing result.
8. “What this case can teach us” and “Why this case may not apply today”.

Feature distance at or below 0.20 of its configured scale is labelled close for
display. Up to three main close features sort by descending scoring weight, then
distance and identifier. Up to three main mismatches sort by their weighted
distance contribution, then identifier. Missing scored comparisons are reported
separately with their existing missing-data penalty; excluded features do not
become scored mismatches. The full feature comparison list remains available in
the read-only web API's `case_card` object.

The 0.20 display boundary is **not** a new similarity threshold or a statistical
significance claim. Case generation does not call the similarity function, select
dates, change weights, reorder matches or modify saved artifacts. All score and
ranking calculations remain unchanged.

Lessons are fixed templates selected by the signs and availability of the recorded
5/20-session benchmark returns: both negative, both positive, mixed/flat, only one
available, or neither available. A positive endpoint with negative drawdown also
notes that gains included a decline from a running peak. There is no invented news,
event narrative, explanation of causation, LLM interpretation or forecast. Historical
outcomes only affect this retrospective lesson; they never enter scoring features.

Limitations identify market-only versus company-specific scope, missing data,
known mismatches, low/unknown regime confidence and the selected sample size.
An individual case cannot establish today's outcome probabilities. Research/AI
evidence packages keep their existing bounded top-three summaries without the
new explanatory card text. No OpenAI request is made.

`HistoricalRegimeSnapshot.training_features()` exports features only.
`HistoricalAnalogue.training_row()` explicitly joins these to separately named
`future_1d_return`, `future_5d_return`, `future_20d_return`, `future_60d_return` and
matching `benchmark_future_*` labels. Missing labels stay null. Future ML should
consume these boundaries with point-in-time data and time-separated evaluation;
no training is performed now.
