from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.adapters.csv_portfolio import (
    CsvPortfolioRepository,
    PortfolioFormatError,
)


def test_reads_example_portfolio() -> None:
    holdings = CsvPortfolioRepository(Path("data/portfolio.example.csv")).list_holdings()

    assert len(holdings) == 2
    assert holdings[0].symbol == "MSFT"
    assert holdings[0].exchange == "NASDAQ"
    assert holdings[0].quantity == Decimal("2")


def test_rejects_missing_columns(tmp_path: Path) -> None:
    portfolio = tmp_path / "bad.csv"
    portfolio.write_text("symbol,quantity\nMSFT,2\n", encoding="utf-8")

    with pytest.raises(PortfolioFormatError, match="Missing CSV columns"):
        CsvPortfolioRepository(portfolio).list_holdings()
