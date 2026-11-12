import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from investment_assistant import cli
from investment_assistant.adapters.investor_profiles import (
    InvestorProfileError,
    InvestorProfileRepository,
)


def test_partial_profile_preserves_unknown_values_and_serializes_supplied_values(
    tmp_path: Path,
) -> None:
    repository = InvestorProfileRepository(tmp_path)

    profile, path = repository.update(
        "spcx",
        {
            "already_owns_stock": True,
            "average_purchase_price": "42.50",
            "risk_tolerance": "medium",
        },
    )

    assert path == tmp_path / "SPCX.json"
    assert profile.amount_invested is None
    payload = profile.evidence_payload()
    assert payload["status"] == "configured"
    assert payload["supplied_values"]["average_purchase_price"] == "42.50"
    assert "amount_invested" in payload["missing_values"]


def test_profile_update_preserves_omitted_fields_and_null_clears_field(
    tmp_path: Path,
) -> None:
    repository = InvestorProfileRepository(tmp_path)
    repository.update(
        "SPCX",
        {"risk_tolerance": "high", "investment_objective": "Long-term growth"},
    )

    updated, _ = repository.update("SPCX", {"risk_tolerance": None})

    assert updated.risk_tolerance is None
    assert updated.investment_objective == "Long-term growth"


def test_profile_rejects_unknown_fields_and_out_of_range_percentage(
    tmp_path: Path,
) -> None:
    repository = InvestorProfileRepository(tmp_path)

    with pytest.raises(InvestorProfileError, match="Unknown profile field"):
        repository.update("SPCX", {"secret_guess": "value"})
    with pytest.raises(InvestorProfileError, match="maximum_portfolio_percentage"):
        repository.update("SPCX", {"maximum_portfolio_percentage": "120"})


def test_profile_file_command_does_not_print_confidential_values(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps(
            {
                "ticker": "SPCX",
                "amount_invested": "12345.67",
                "reason_for_investing": "Private long-term reason",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    assert cli.run_profile("SPCX", source) == 0

    output = capsys.readouterr().out
    assert "12345.67" not in output
    assert "Private long-term reason" not in output
    assert "confidential values were not displayed" in output
    stored = (tmp_path / "profiles" / "SPCX.json").read_text(encoding="utf-8")
    assert "12345.67" in stored


def test_interactive_profile_retries_only_invalid_field_and_keeps_prior_answers(
    monkeypatch: Any, capsys: Any
) -> None:
    answers = iter(
        [
            "yes",
            "-5",
            "125.50",
            "",
            "",
            "",
            "",
            "extreme",
            "medium",
            "",
            "101%",
            "10%",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    prompts: list[str] = []

    def hidden_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(cli.getpass, "getpass", hidden_input)

    updates = cli._prompt_profile_updates()

    assert updates["already_owns_stock"] is True
    assert updates["average_purchase_price"] == Decimal("125.50")
    assert updates["risk_tolerance"].value == "medium"
    assert updates["maximum_portfolio_percentage"] == Decimal("10")
    assert sum("Already owns stock" in prompt for prompt in prompts) == 1
    assert sum("Average purchase price" in prompt for prompt in prompts) == 2
    assert sum("Risk tolerance" in prompt for prompt in prompts) == 2
    assert sum("Maximum portfolio percentage" in prompt for prompt in prompts) == 2
    output = capsys.readouterr().out
    assert "Required format: a non-negative number" in output
    assert "Required format: low, medium, or high" in output
    assert "optionally followed by %" in output


@pytest.mark.parametrize("entered", ["10", "10%"])
def test_interactive_percentage_accepts_plain_or_percent_suffix(
    entered: str, tmp_path: Path
) -> None:
    normalized = cli._parse_profile_input("maximum_portfolio_percentage", entered)
    repository = InvestorProfileRepository(tmp_path)
    profile, path = repository.update("SPCX", {"maximum_portfolio_percentage": normalized})

    assert normalized == Decimal("10")
    assert profile.maximum_portfolio_percentage == Decimal("10")
    assert "%" not in path.read_text(encoding="utf-8")
