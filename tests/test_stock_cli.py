from datetime import date
from decimal import Decimal
from typing import Any

from investment_assistant.cli import print_stock_snapshot
from investment_assistant.domain.models import StockMarketSnapshot


def test_prints_stock_snapshot_clearly(capsys: Any) -> None:
    snapshot = StockMarketSnapshot(
        ticker="NVDA",
        as_of=date(2026, 7, 10),
        latest_close=Decimal("164.92"),
        previous_close=Decimal("162.88"),
        daily_change_percent=Decimal("1.2524558"),
        five_day_change_percent=Decimal("-2.345"),
        volume=192_345_678,
    )

    print_stock_snapshot(snapshot)
    output = capsys.readouterr().out

    assert "Stock:              NVDA" in output
    assert "Latest close:       164.92" in output
    assert "Daily change:       +1.25%" in output
    assert "5-day change:       -2.34%" in output
    assert "Volume:             192,345,678" in output
