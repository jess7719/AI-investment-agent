# Deep-report validation and paid-response recovery

The application now separates receiving a paid API response from approving an investment
report.

## Durable response lifecycle

Immediately after a Responses API result is received, the application writes a private atomic
pending artifact containing:

- input, cached-input, output, and total tokens
- model and response ID
- request timestamp
- original structured response
- bounded validation evidence and allowed references

No API key or environment secret is included.

The pending artifact becomes one of:

- `reports/YYYY-MM-DD/TICKER-deep.json` after successful validation
- `reports/YYYY-MM-DD/rejected/TICKER-deep-TIMESTAMP.json` after fatal validation

A rejected artifact is marked `approved_for_investment_use: false`. A separate
`-validation.json` file records the errors and preserved usage.

## Validation severity

### Fatal

The report is not approved or rendered as the final report. The response is retained under
`rejected/`. Examples include malformed structured output, invented citations, unsupported
event clusters, claims of reading unavailable full text, unsupported numerical facts, invalid
enum or mandatory schema fields, corrupted JSON, and long copied source passages.

### Repairable locally

The application repairs the structured response without another API request. Examples include:

- sentences above the 45-word beginner target
- excessive decimal precision
- large whole-number volume formatting
- embedded Markdown heading or bullet prefixes
- duplicate bullet text
- repeated standard disclaimers

Sentences containing 46–70 words are split locally. Sentences above 70 words are split and also
flagged for review. Protected decimal values, common abbreviations, evidence markers, and URLs
are not split internally.

### Warning only

The report is saved, but the terminal shows warnings for cautious or repetitive wording,
unexplained jargon, headline-like explanations, unavailable optional data, profile guidance
that could be more specific, or a report longer than preferred.

## User-facing number formatting

Original model precision remains in the structured response artifact. Rendered Markdown uses:

- two decimal places for prices and percentages
- comma-separated whole numbers for large trading volumes
- whole-number probability ranges

## Validate an existing response without OpenAI

```powershell
investment-assistant validate-report PATH
```

This command loads the saved validation context, performs fatal checks, applies deterministic
repairs, renders Markdown beside the JSON file, and makes no OpenAI request.

## Paid-response terminal log

After a response, success or rejection, the command prints:

- whether an API request completed
- input, cached-input, output, and total tokens
- model, response ID, and request timestamp
- validation status (`passed`, `repaired`, or `rejected`)
- approved report path or rejected-response path

The unrecoverable SPCX response created before this design was introduced was never written to
disk. Because that request used `store=False`, this application cannot recover it without a new
paid request. It does not attempt to do so.
