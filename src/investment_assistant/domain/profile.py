"""Validated personal investor-profile data with every field optional."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class RiskTolerance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


NonNegativeMoney = Annotated[Decimal, Field(ge=0)]
Percentage = Annotated[Decimal, Field(ge=0, le=100)]
ProfileText = Annotated[str, Field(min_length=1, max_length=1_000)]


class InvestorProfile(BaseModel):
    """A partial profile; missing values remain explicitly unknown."""

    model_config = ConfigDict(extra="forbid")

    ticker: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")]
    already_owns_stock: bool | None = None
    average_purchase_price: NonNegativeMoney | None = None
    amount_invested: NonNegativeMoney | None = None
    current_position_value: NonNegativeMoney | None = None
    intended_additional_investment: NonNegativeMoney | None = None
    intended_holding_period: ProfileText | None = None
    risk_tolerance: RiskTolerance | None = None
    maximum_acceptable_loss: ProfileText | None = None
    maximum_portfolio_percentage: Percentage | None = None
    investment_objective: ProfileText | None = None
    reason_for_investing: ProfileText | None = None
    position_description: ProfileText | None = None
    trading_preference: ProfileText | None = None
    investment_currency: ProfileText | None = None

    @property
    def configured_fields(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in self.__class__.model_fields
            if name != "ticker" and getattr(self, name) is not None
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.configured_fields)

    def evidence_payload(self) -> dict[str, object]:
        """Serialize only supplied values and explicitly list what remains unknown."""

        supplied: dict[str, object] = {}
        missing: list[str] = []
        for name in self.__class__.model_fields:
            if name == "ticker":
                continue
            value = getattr(self, name)
            if value is None:
                missing.append(name)
            elif isinstance(value, Decimal):
                supplied[name] = str(value)
            elif isinstance(value, StrEnum):
                supplied[name] = value.value
            else:
                supplied[name] = value
        return {
            "status": "configured" if supplied else "not configured",
            "ticker": self.ticker,
            "supplied_values": supplied,
            "missing_values": missing,
            "instruction": (
                "Use supplied values when relevant. Never infer or fill missing values."
            ),
        }
