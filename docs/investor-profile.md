# Private investor profiles (Phase 3)

Each ticker can have one partial personal profile. Profiles are stored locally under:

```text
profiles/TICKER.json
```

The entire `profiles/` directory is excluded by `.gitignore`.

## Interactive creation or editing

```powershell
investment-assistant profile SPCX
```

All answers use hidden terminal input. Existing values are not displayed. Leaving a prompt
blank keeps its current value.

Each answer is validated immediately. An invalid answer shows the required format and repeats
only that field; previously accepted answers do not need to be entered again.

To clear a field, use a JSON file with that field explicitly set to `null`.

## Editing from a private JSON file

```powershell
investment-assistant profile SPCX --file PATH
```

Only fields in the file are merged into the existing profile. The command validates the ticker,
monetary values, risk tolerance, and percentage ranges. It reports the number of configured
fields but does not print their values.

Supported fields:

- `already_owns_stock`
- `average_purchase_price`
- `amount_invested`
- `current_position_value`
- `intended_additional_investment`
- `intended_holding_period`
- `risk_tolerance` (`low`, `medium`, or `high`)
- `maximum_acceptable_loss` (include its amount or percentage unit)
- `maximum_portfolio_percentage` (0 through 100; both `10` and `10%` are accepted and stored
  as the normalized value `10`)
- `investment_objective`
- `reason_for_investing`
- `position_description` (for example, intentionally small starter position)
- `trading_preference` (for example, avoid frequent short-term trading)
- `investment_currency` (for example, GBP)

Every field is optional. Missing values remain unknown and are explicitly listed as missing in
the evidence sent to the model.

## Report integration

Configured values are supplied only to:

- `investment-assistant news-report TICKER`
- `investment-assistant analyse TICKER --deep`

The model is instructed to use supplied values when relevant and never infer missing values.
Dry-runs display only profile status and the number of supplied fields.

Daily reports remain cached even if the profile changes. This prevents an unexpected paid API
request. Use `--force` only when you deliberately want to regenerate an existing report with
the updated profile.

## Privacy limitations

This is private local storage, not an encrypted password vault. Protect access to the computer
and OneDrive account. Do not commit, email, or share profile files. API report generation sends
the supplied profile fields to OpenAI together with the bounded research evidence.
