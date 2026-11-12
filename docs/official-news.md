# Official company news sources

The news command can combine four public metadata sources:

- SEC EDGAR company-submissions metadata
- a verified company investor-relations release listing
- GDELT article metadata
- Google News RSS metadata

For SpaceX, the local company profile maps `SPCX` to:

- legal name: `Space Exploration Technologies Corp.`
- SEC CIK: `1181412`
- verified IR host: `ir.spacex.com`
- IR release listing: `https://ir.spacex.com/updates/`

Run the normal relevance-first ranking:

```powershell
investment-assistant news SPCX
```

Run the source-confidence-first ranking:

```powershell
investment-assistant news SPCX --official-first
```

Each result has three independent scores: relevance, financial importance, and
source confidence. Source confidence ranks SEC filings first, verified company
releases second, factual financial reporting third, then analyst commentary,
opinion, and unknown or aggregated sources. An opinion label means the item is
commentary, not verified fact.

Ranking is a weighted balance, not a source-confidence sort. Normal mode weights
relevance 35%, importance 35%, source confidence 20%, and recency 10%.
`--official-first` increases source confidence to 30% and uses 25% relevance,
while importance remains 35% and recency remains 10%.

Filing importance is type-aware. Annual and quarterly reports, registration and
prospectus filings, earnings 8-Ks, major-transaction 8-Ks, and major-financing
filings receive high potential importance. Form 3 and routine filings stay low.
A Form 4 reaches medium importance only for an explicitly coded open-market
purchase or sale (`P` or `S`) involving at least 10,000 shares or at least
$100,000 of reported value. Missing details never produce an insider-sentiment
inference.

The SEC adapter requests the public submissions JSON endpoint and stores only
filing metadata. Set `SEC_USER_AGENT` in `.env` to an application name and a
contact email so the request identifies the client. This is not an API key.

The IR adapter fetches only the configured release-list page. It accepts links
only when their host matches the verified IR domain and does not fetch release
bodies. The other adapters likewise retain metadata and any summary supplied by
the public source; they do not retrieve full article text.

No source used by this feature requires a paid API or API key. The disabled FT
and university database adapters remain unavailable until legitimate credentials
and programmatic-use permission are provided.

Run all offline tests:

```powershell
python -m pytest -q --basetemp .test-tmp -p no:cacheprovider
```
