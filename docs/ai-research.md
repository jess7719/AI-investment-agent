# Evidence-grounded AI research reports

The `analyse` command combines data already collected by this application. It does
not give OpenAI web-search tools and does not send article or filing body text.

Preview the request without an OpenAI call or charge:

```powershell
investment-assistant analyse SPCX --dry-run
```

After reviewing the preview, create the report:

```powershell
investment-assistant analyse SPCX
```

The report is saved to `reports/YYYY-MM-DD/TICKER.md`. If that file already
exists, the command prints and reuses it without collecting data or calling
OpenAI. Only use `--force` when intentionally replacing today's report and
accepting the possibility of another API charge.

The model comes from `OPENAI_MODEL`. The default is `gpt-5.6-luna`, a current
cost-sensitive model with Responses API and structured-output support. The
application uses the official Python SDK's `responses.parse` method with a
Pydantic schema, a 3,000-token output ceiling, a 60-second timeout, and at most
one SDK-managed retry.

Evidence sent to the model is limited to one market snapshot and at most eight
ranked information items. Headlines are limited to 240 characters and legally
provided summaries to 400 characters. Items receive stable references such as
`[News 1]` and `[Filing 1]`. The completed model response is rejected if it cites
an unknown reference or claims fuller evidence that was not supplied.

The API key is loaded explicitly from the existing project-root `.env` by `python-dotenv`,
even if the installed command is launched from another directory.
The key is never included in evidence, terminal output, reports, or errors.

Common safe errors distinguish missing configuration, authentication, billing or
credit problems, rate limits, timeouts/network failures, malformed structured
responses, unavailable market data, and unavailable news data.

Offline verification:

```powershell
python -m pytest -q --basetemp .test-tmp -p no:cacheprovider
ruff check src tests
```
