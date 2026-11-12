# Daily news report (Phase 1.1)

The `news-report` command turns the Phase 1 evidence into an event-based daily report for a
beginner investor. It does not count repeated headlines as separate developments. It uses the
existing official filing extracts, public article evidence, manual notes, source scores, event
clusters, and market data.

## Safe preview

```powershell
investment-assistant news-report SPCX --dry-run
```

The preview collects and prepares evidence, shows the selected event clusters and separate
noise candidates, estimates the request size, and makes no OpenAI request. It never prints the
API key.

## Generate the report

```powershell
investment-assistant news-report SPCX
```

This uses the OpenAI Responses API and therefore may incur API cost. The structured response is
validated before it is saved to:

```text
reports/YYYY-MM-DD/TICKER-news.md
```

The same daily report is reused unless `--force` is supplied. `--force` can create another paid
request, so use it only when the evidence changed enough to justify regenerating the report.

## Evidence and quality controls

- At most three distinct main event clusters can appear.
- Every material statement must use a supplied reference such as `[News 1]`, `[Filing 1]`, or
  `[Market Data]`.
- The reported evidence type must match the application's evidence-depth label.
- Opinion or repeated coverage may be shown as noise but cannot establish company facts.
- No web-search tool is enabled in the OpenAI request.
- Full copyrighted articles and complete SEC filings are not sent to the model.
- The model cannot give a target price, guarantee a return, or place a trade.

## Investor profile limitation

Personal investor profiles belong to a later, separately approved phase. Until a profile is
configured, the command explicitly tells the model not to assume ownership, cost basis,
position size, risk tolerance, or financial goals. The action section therefore gives separate
frameworks for someone who already owns the stock and someone who does not.
