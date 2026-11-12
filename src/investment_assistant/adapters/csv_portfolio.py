"""Read a beginner-friendly portfolio from a CSV file."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from investment_assistant.domain.models import Holding


class PortfolioFormatError(ValueError):
    """Raised when a portfolio row is missing or invalid."""


class CsvPortfolioRepository:
    REQUIRED_COLUMNS = {
        "symbol",
        "exchange",
        "company",
        "quantity",
        "average_cost",
        "currency",
        "thesis",
    }

    def __init__(self, path: Path) -> None:
        self.path = path

    def list_holdings(self) -> list[Holding]:
        with self.path.open(newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            missing = self.REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                names = ", ".join(sorted(missing))
                raise PortfolioFormatError(f"Missing CSV columns: {names}")

            return [self._parse_row(row, row_number) for row_number, row in enumerate(reader, 2)]

    @staticmethod
    def _parse_row(row: dict[str, str | None], row_number: int) -> Holding:
        try:
            symbol = (row["symbol"] or "").strip().upper()
            exchange = (row["exchange"] or "").strip().upper()
            company = (row["company"] or "").strip()
            quantity = Decimal(row["quantity"] or "")
            average_cost = Decimal(row["average_cost"] or "")
            currency = (row["currency"] or "").strip().upper()
            thesis = (row["thesis"] or "").strip()
        except (InvalidOperation, KeyError) as error:
            raise PortfolioFormatError(f"Invalid number or field on row {row_number}") from error

        if not symbol or not exchange or not company or not currency:
            raise PortfolioFormatError(f"Required text is empty on row {row_number}")
        if quantity <= 0 or average_cost < 0:
            raise PortfolioFormatError(f"Quantity/cost is invalid on row {row_number}")

        return Holding(symbol, exchange, company, quantity, average_cost, currency, thesis)
