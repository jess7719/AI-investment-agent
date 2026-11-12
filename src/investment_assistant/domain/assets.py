"""Asset classification and verified product metadata used by research workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AssetType(StrEnum):
    EQUITY = "equity"
    COMMODITY_ETP = "commodity_etp"
    ETF = "etf"
    CRYPTO = "crypto"
    INDEX = "index"


@dataclass(frozen=True, slots=True)
class AssetProfile:
    ticker: str
    asset_type: AssetType
    name: str
    underlying_symbol: str | None = None
    underlying_name: str | None = None


ASSET_PROFILES: dict[str, AssetProfile] = {
    "BRNT.L": AssetProfile(
        ticker="BRNT.L",
        asset_type=AssetType.COMMODITY_ETP,
        name="WisdomTree Brent Crude Oil",
        underlying_symbol="BZ=F",
        underlying_name="Brent crude oil futures",
    ),
    "SPCX": AssetProfile("SPCX", AssetType.EQUITY, "SpaceX"),
}


def asset_profile_for(ticker: str) -> AssetProfile:
    normalized = ticker.strip().upper()
    return ASSET_PROFILES.get(
        normalized,
        AssetProfile(normalized, AssetType.EQUITY, normalized),
    )
