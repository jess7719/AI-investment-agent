# Compact deep analysis

The deep-analysis command turns the application's existing market, news, filing, fundamental,
and investor-profile evidence into a compact decision-support report. It does not browse from
inside the OpenAI request and does not execute trades.

## Dry run

```powershell
investment-assistant analyse SPCX --deep --dry-run
```

The dry run collects and selects evidence, shows evidence depth and selected event clusters,
estimates input size, and constructs no OpenAI client. It makes no paid request.

## Paid report

```powershell
investment-assistant analyse SPCX --deep
```

Successful reports are saved as:

```text
reports/YYYY-MM-DD/TICKER-deep.md
reports/YYYY-MM-DD/TICKER-deep.json
```

The compact structured report contains:

1. Conclusion
2. Up to five key evidence items with references
3. A 1-2 week view with concise base, bull, and bear cases
4. A 1-3 month view
5. The long-term thesis
6. Profile-aware investor action
7. Positive, negative, and upcoming thesis triggers
8. Separate confidence assessments
9. Up to five limitations

Ordinary lists contain no more than three items. The model is instructed to stay below 3,500
output tokens. The request has a 6,000-token hard safety ceiling to avoid losing an otherwise
valid response at the boundary. A lower ceiling can be selected with:

```dotenv
OPENAI_DEEP_MAX_OUTPUT_TOKENS=5000
```

Values below 1,000 are raised to 1,000. Values above 6,000 are capped at 6,000.

## Validation and recovery

The application validates the compact schema, citations, numerical facts, evidence-access
language, and copied source passages. Formatting problems such as long sentences and excessive
decimal precision are repaired locally without another model request.

Usage, cached-token usage, model, response ID, response status, incomplete details, and request
time are retained. Fatal failures save the raw response and a validation summary under the
dated `rejected` directory. Existing schema-version-1 rejected artifacts are never overwritten;
future compact artifacts use schema version 2.

An existing same-day approved report is reused unless the user explicitly supplies `--force`.
