"""Private local JSON storage for partial investor profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from investment_assistant.domain.profile import InvestorProfile
from investment_assistant.services.news_service import TICKER_PATTERN


class InvestorProfileError(RuntimeError):
    """Safe profile validation or storage error."""


class InvestorProfileRepository:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, ticker: str) -> Path:
        normalized = _normalize_ticker(ticker)
        return self._root / f"{normalized}.json"

    def load(self, ticker: str) -> InvestorProfile:
        normalized = _normalize_ticker(ticker)
        path = self.path_for(normalized)
        if not path.exists():
            return InvestorProfile(ticker=normalized)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return InvestorProfile.model_validate(value)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise InvestorProfileError(
                f"Profile for {normalized} is invalid or unreadable."
            ) from error

    def update(self, ticker: str, fields: dict[str, object]) -> tuple[InvestorProfile, Path]:
        normalized = _normalize_ticker(ticker)
        unknown = set(fields) - set(InvestorProfile.model_fields)
        if unknown:
            raise InvestorProfileError(
                f"Unknown profile field(s): {', '.join(sorted(unknown))}."
            )
        if "ticker" in fields and str(fields["ticker"]).strip().upper() != normalized:
            raise InvestorProfileError("Profile file ticker does not match the command ticker.")
        current = self.load(normalized)
        merged = current.model_dump(mode="python")
        merged.update({key: value for key, value in fields.items() if key != "ticker"})
        merged["ticker"] = normalized
        try:
            profile = InvestorProfile.model_validate(merged)
        except ValidationError as error:
            raise InvestorProfileError(_validation_message(error)) from error
        path = self.path_for(normalized)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                profile.model_dump_json(indent=2),
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        except OSError as error:
            raise InvestorProfileError(
                f"Could not save profile for {normalized}."
            ) from error
        return profile, path


def load_profile_file(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvestorProfileError(
            "Profile file must be readable UTF-8 JSON."
        ) from error
    if not isinstance(value, dict):
        raise InvestorProfileError("Profile file must contain one JSON object.")
    return {str(key): item for key, item in value.items()}


def _normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if not TICKER_PATTERN.fullmatch(normalized):
        raise InvestorProfileError(
            "Ticker must contain only letters, numbers, '.', '-', '^' or '='."
        )
    return normalized


def _validation_message(error: ValidationError) -> str:
    first = error.errors()[0]
    field = ".".join(str(item) for item in first.get("loc", ())) or "profile"
    return f"Invalid value for {field}: {first.get('msg', 'validation failed')}."
