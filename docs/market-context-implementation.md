# Market context implementation report

Implemented deterministic Market Regime and Historical Analogue engines for the
listed-equity workflow, with replaceable provider ports, raw calculation evidence,
CLI commands, private JSON artifacts, read-only web panels and saved-context research
evidence integration. Existing advice rules, report validation and recovery logic
were not modified.

## Files added

- `src/investment_assistant/domain/regime.py`
- `src/investment_assistant/domain/analogues.py`
- `src/investment_assistant/ports/regime.py`
- `src/investment_assistant/adapters/yfinance_regime.py`
- `src/investment_assistant/services/market_regime.py`
- `src/investment_assistant/services/historical_features.py`
- `src/investment_assistant/services/historical_analogues.py`
- `src/investment_assistant/services/context_artifacts.py`
- `src/investment_assistant/services/context_cli.py`
- `src/investment_assistant/web/templates/market_context.html`
- `tests/test_market_regime.py`
- `tests/test_historical_analogues.py`
- `tests/test_context_integration.py`
- `tests/run_offline.py`
- `docs/market-regime.md`
- `docs/historical-analogues.md`
- `docs/market-context-implementation.md` (this report)

## Files modified

- `src/investment_assistant/config.py`: centralized thresholds, weights/scales and artifact root.
- `src/investment_assistant/cli.py`: regime/analogues commands and historical dates.
- `src/investment_assistant/services/research_service.py`: attach saved deterministic context.
- `src/investment_assistant/web/app.py`: read-only saved-context endpoint and template data.
- `src/investment_assistant/web/templates/workspace.html`: include the two context panels.
- `README.md` and `README.en.md`: commands and documentation links.
- `.gitignore`: exclude private `data/research/` artifacts.

## Dimensions, sources and unavailable evidence

Classified dimensions: broad equity trend, volatility, Treasury-yield direction and
risk sentiment. Each retains rules, sources, dates, raw windows and confidence.
Monetary policy, inflation and growth have domain/port support and explicitly return
unknown; no official macro provider or classification rules are enabled.

Live composition uses the **existing Yahoo Finance/yfinance infrastructure**: SPY,
QQQ, ^VIX, ^TNX and the requested target equity. No new external provider was added.
Implementation verification used synthetic fixtures, not live market requests.
Yahoo history is not a certified point-in-time vintage archive; stored raw windows
make recorded computations reproducible but do not remove vendor revision/split or
survivorship limitations. Returns are price returns, not total returns.

## Analogues and reproducibility

Features: broad-market 5/20/60-session returns; VIX level/trailing percentile;
10-year yield level/20-session change; risk score; target 20/60-session momentum,
20-session realised volatility and volume ratio. Features with inadequate historical
coverage are excluded with reasons. New listings fall back to market_regime_only;
their company-specific outcomes are unavailable, while benchmark outcomes remain.

Similarity is `100*(1-min(1,D+P))`: D is the weighted mean of clipped absolute
feature differences divided by fixed configured scales; P is the configured category
mismatch penalty. Missing comparisons incur distance 1, never zero imputation.
Group defaults are market trend 20%, volatility 15%, rates 15%, risk 15%, target
momentum 15%, target volatility 10%, volume 10%. Actual normalized weights/scales
are saved. No recency weighting is used; ties prefer earlier dates explicitly.

Historical forward outcomes use 1/5/20/60 benchmark trading sessions, with asset
return, benchmark return, excess return and maximum drawdown. Summary statistics
retain negative cases and show per-window sample sizes and small-sample warnings.

Look-ahead protections: dual observation/availability cutoffs; trailing-only
features; separate forward labels; fixed scales rather than whole-history fitted
normalization; as-of-bounded outcomes; 120-session default embargo separating old
outcomes from the current 60-session market feature window; date validation and
explicit tests mutating future data. Selected matches are separated by 20 sessions
by default, although longer outcome windows can still overlap.

## Verification

```powershell
.venv/Scripts/python.exe -u tests/run_offline.py -q -o faulthandler_timeout=45 --basetemp test-output-regime-full-v3
.venv/Scripts/python.exe -m ruff check .
```

Result: **228 passed in 79.10 seconds; Ruff: all checks passed**. The suite includes
50 new test cases and the previous 178 cases. Two non-failing warnings concerned
AnyIO pytest assertion rewriting and the existing Starlette/httpx deprecation.

Windows asyncio's internal loopback sockets required running the suite outside the
restricted socket sandbox. The offline launcher permitted loopback while blocking
external sockets, live yfinance history and paid OpenAI request methods. Earlier
sandbox-stalled test processes were stopped. No OpenAI API request was made during
implementation, examples or verification. No ML dependency was introduced.

Coverage includes trend/volatility/rates, conflict and stale confidence, missing
macro, publication-vintage filtering, identical/dissimilar vectors, weighting,
ranking, outcome/excess/drawdown arithmetic, negative cases, no future leakage,
new-listing fallback, sample warnings, missing data, historical CLI dates,
JSON persistence, evidence package round trips, read-only web views, and secret
non-disclosure.

## Example regime output

**Synthetic offline fixture — not current or historical financial observations.**
Excerpt from the actual formatter output:

```text
Market regime as of 2024-05-17 (benchmark: SPY)
Equity trend: Strong uptrend
Volatility: Normal
Rates: Stable
Monetary policy: Unknown
Inflation: Unknown
Growth: Unknown
Risk sentiment: Risk on

Confidence:
- equity trend: high
- volatility: low
- rates: low
- monetary policy: unknown
- inflation: unknown
- growth: unknown
- risk sentiment: low
```

The full terminal output also shows dated indicator values and missing-data status;
JSON retains exact raw windows and formulas.

## Example analogue output

**Synthetic offline fixture — not financial data or a forecast.** Excerpt:

```text
Historical analogues for TEST as of 2024-05-17
Mode: asset_specific; asset-specific: available

Top matches (sample 3; insufficient historical sample):
1. 2023-09-22 | similarity 96.5/100
   5-day historical forward outcome: asset -2.97%; benchmark +1.51%; excess -4.48%
   20-day historical forward outcome: asset -12.85%; benchmark +6.17%; excess -19.03%
2. 2023-11-15 | similarity 96.4/100
   5-day historical forward outcome: asset +4.21%; benchmark +1.51%; excess +2.70%
   20-day historical forward outcome: asset +5.74%; benchmark +6.17%; excess -0.43%
3. 2023-07-06 | similarity 96.3/100
   5-day historical forward outcome: asset -3.14%; benchmark +1.51%; excess -4.65%
   20-day historical forward outcome: asset -12.75%; benchmark +6.17%; excess -18.93%

Historical forward outcomes (selected matches):
- 20-day: asset n=3, median -12.75%, worst -12.85%, best +5.74%; benchmark n=3, median +6.17%

Important: These are historical comparisons, not forecasts.
```

See [market regime rules](market-regime.md) and
[analogue formula and limitations](historical-analogues.md) for the complete design,
configuration, coverage policy, future ML row export and operational instructions.
