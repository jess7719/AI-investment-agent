"""Retrieve one stock's daily market data with yfinance."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from math import sqrt
from pathlib import Path
from typing import Any

import yfinance as yf

from investment_assistant.domain.context import (
    ComparisonReturn,
    CommodityETPContext,
    CommodityProductContext,
    FundamentalContext,
    FundamentalMetric,
    FuturesCurvePoint,
    MarketContext,
)
from investment_assistant.domain.assets import AssetType, asset_profile_for
from investment_assistant.domain.models import StockMarketSnapshot
from investment_assistant.config import PROJECT_ROOT

DEFAULT_YFINANCE_CACHE = PROJECT_ROOT / ".cache" / "investment-assistant" / "yfinance"


class MarketDataError(RuntimeError):
    """Raised when a usable market snapshot cannot be produced."""


class YFinanceMarketDataAdapter:
    """Convert yfinance history into the application's standard data model."""

    def __init__(
        self,
        ticker_factory: Callable[[str], Any] = yf.Ticker,
        cache_dir: Path | None = None,
    ) -> None:
        # Injection lets tests provide a fake ticker without using the network.
        self._ticker_factory = ticker_factory
        if ticker_factory is yf.Ticker:
            writable_cache = cache_dir or DEFAULT_YFINANCE_CACHE
            writable_cache.mkdir(parents=True, exist_ok=True)
            yf.set_tz_cache_location(str(writable_cache))

    def get_stock_snapshot(self, ticker: str) -> StockMarketSnapshot:
        normalized_ticker = ticker.strip().upper()
        if not normalized_ticker:
            raise MarketDataError("Ticker cannot be empty.")

        try:
            history = self._ticker_factory(normalized_ticker).history(
                period="1mo",
                interval="1d",
                auto_adjust=False,
                actions=False,
                timeout=10,
                raise_errors=True,
            )
        except Exception as error:
            raise MarketDataError(
                f"Could not retrieve market data for {normalized_ticker}: {error}"
            ) from error

        required_columns = {"Close", "Volume"}
        if history is None or history.empty or not required_columns.issubset(history.columns):
            raise MarketDataError(f"No usable daily market data found for {normalized_ticker}.")

        usable_history = history.dropna(subset=["Close", "Volume"])
        if len(usable_history) < 6:
            raise MarketDataError(
                f"At least 6 trading days are required for {normalized_ticker}; "
                f"received {len(usable_history)}."
            )

        latest_row = usable_history.iloc[-1]
        previous_row = usable_history.iloc[-2]
        five_days_ago_row = usable_history.iloc[-6]

        latest_close = Decimal(str(latest_row["Close"]))
        previous_close = Decimal(str(previous_row["Close"]))
        five_days_ago_close = Decimal(str(five_days_ago_row["Close"]))

        if previous_close == 0 or five_days_ago_close == 0:
            raise MarketDataError(f"Cannot calculate percentage changes for {normalized_ticker}.")

        daily_change = (latest_close / previous_close - Decimal("1")) * Decimal("100")
        five_day_change = (latest_close / five_days_ago_close - Decimal("1")) * Decimal(
            "100"
        )

        return StockMarketSnapshot(
            ticker=normalized_ticker,
            as_of=usable_history.index[-1].date(),
            latest_close=latest_close,
            previous_close=previous_close,
            daily_change_percent=daily_change,
            five_day_change_percent=five_day_change,
            volume=int(latest_row["Volume"]),
        )

    def get_market_context(self, ticker: str) -> MarketContext:
        normalized = ticker.strip().upper()
        history = self._history(normalized, "1y")
        usable = history.dropna(subset=["Close", "Volume"])
        if len(usable) < 20:
            raise MarketDataError(f"Insufficient market context for {normalized}.")
        close = usable["Close"]
        volume = usable["Volume"]
        latest = Decimal(str(close.iloc[-1]))
        window_30 = close.iloc[-30:]
        average_volume = int(volume.iloc[-20:].mean())
        returns = close.pct_change().dropna().iloc[-20:]
        volatility = Decimal(str(returns.std() * sqrt(252) * 100)) if len(returns) >= 2 else None
        comparisons = []
        for symbol, label in comparison_symbols(normalized):
            comparisons.append(self._comparison(symbol, label))
        return MarketContext(
            ticker=normalized,
            as_of=usable.index[-1].date(),
            five_day_change_percent=_period_change(close, 5),
            thirty_day_change_percent=_period_change(close, 30),
            ninety_day_change_percent=_period_change(close, 90),
            distance_from_thirty_day_high_percent=(
                latest / Decimal(str(window_30.max())) - Decimal("1")
            )
            * Decimal("100"),
            distance_from_thirty_day_low_percent=(
                latest / Decimal(str(window_30.min())) - Decimal("1")
            )
            * Decimal("100"),
            average_twenty_day_volume=average_volume,
            latest_volume_vs_average_percent=(
                Decimal(str(volume.iloc[-1])) / Decimal(average_volume) - Decimal("1")
            )
            * Decimal("100")
            if average_volume
            else None,
            recent_annualized_volatility_percent=volatility,
            comparisons=tuple(comparisons),
            data_quality="third-party market data; trading-session windows; verify if material",
        )

    def get_fundamental_context(self, ticker: str) -> FundamentalContext:
        normalized = ticker.strip().upper()
        if asset_profile_for(normalized).asset_type is not AssetType.EQUITY:
            raise MarketDataError(
                f"Company fundamentals do not apply to {normalized}'s asset type."
            )
        now = datetime.now(timezone.utc)
        try:
            info = dict(self._ticker_factory(normalized).info or {})
        except Exception:
            info = {}
        definitions = (
            ("revenue", "totalRevenue", "currency"),
            ("revenue growth", "revenueGrowth", "ratio"),
            ("net income", "netIncomeToCommon", "currency"),
            ("operating margin", "operatingMargins", "ratio"),
            ("free cash flow", "freeCashflow", "currency"),
            ("cash", "totalCash", "currency"),
            ("debt", "totalDebt", "currency"),
            ("shares outstanding", "sharesOutstanding", "shares"),
            ("market capitalisation", "marketCap", "currency"),
            ("P/E", "trailingPE", "multiple"),
            ("price-to-sales", "priceToSalesTrailing12Months", "multiple"),
        )
        currency = str(info.get("currency") or "").strip() or None
        metrics = tuple(
            _fundamental_metric(name, info.get(key), unit, now, currency=currency)
            for name, key, unit in definitions
        )
        peer_metrics: tuple[FundamentalMetric, ...] = ()
        peer = peer_symbol(normalized)
        if peer:
            try:
                peer_info = dict(self._ticker_factory(peer).info or {})
            except Exception:
                peer_info = {}
            peer_metrics = tuple(
                _fundamental_metric(
                    f"{peer} {name}",
                    peer_info.get(key),
                    unit,
                    now,
                    currency=str(peer_info.get("currency") or "").strip() or None,
                )
                for name, key, unit in definitions
                if name in {"revenue growth", "operating margin", "P/E", "price-to-sales"}
            )
        return FundamentalContext(
            ticker=normalized,
            metrics=metrics,
            peer_metrics=peer_metrics,
            source="Yahoo Finance/yfinance third-party snapshot",
            overall_quality=(
                "available but unverified; confirm against official filings"
                if info
                else "unavailable"
            ),
        )

    def get_commodity_etp_context(self, ticker: str) -> CommodityETPContext:
        """Collect bounded commodity and product evidence for configured commodity ETCs."""

        normalized = ticker.strip().upper()
        profile = asset_profile_for(normalized)
        if profile.asset_type is not AssetType.COMMODITY_ETP or not profile.underlying_symbol:
            raise MarketDataError(f"No commodity ETP configuration exists for {normalized}.")
        history = self._history(profile.underlying_symbol, "1y").dropna(subset=["Close"])
        if len(history) < 20:
            raise MarketDataError(f"Insufficient Brent futures context for {normalized}.")
        close = history["Close"]
        front_price = Decimal(str(close.iloc[-1]))
        as_of = history.index[-1].date()
        now = datetime.now(timezone.utc)
        product = _commodity_product_context(normalized)
        return CommodityETPContext(
            ticker=normalized,
            underlying_name=profile.underlying_name or "underlying commodity",
            underlying_symbol=profile.underlying_symbol,
            as_of=as_of,
            spot_price=None,
            front_month_price=front_price,
            five_day_change_percent=_period_change(close, 5),
            thirty_day_change_percent=_period_change(close, 30),
            ninety_day_change_percent=_period_change(close, 90),
            futures_curve=(
                FuturesCurvePoint(
                    contract="front month continuous Brent futures",
                    maturity="front month",
                    price=front_price,
                    currency="USD per barrel",
                    as_of=now,
                    source="Yahoo Finance/yfinance third-party market data",
                    status="available; continuous contract, verify against ICE if material",
                ),
                FuturesCurvePoint(
                    contract="second-month Brent futures",
                    maturity="second month",
                    price=None,
                    currency="USD per barrel",
                    as_of=now,
                    source="not retrieved",
                    status="unavailable",
                ),
            ),
            curve_structure="unavailable",
            roll_yield_direction="unavailable",
            curve_note=(
                "A second dated contract was not reliably available, so contango, "
                "backwardation, and roll-yield direction cannot be inferred."
            ),
            product=product,
            liquidity_evidence={
                "exchange_trading_volume": "supplied in market_data",
                "bid_ask_spread": "unavailable",
                "underlying_market_liquidity": "not independently measured",
                "market_maker_liquidity": "unavailable",
                "interpretation_rule": (
                    "Do not infer poor liquidity from low exchange volume alone; bid-ask, "
                    "underlying-market, and market-maker evidence are also needed."
                ),
                "assessment": "incomplete",
            },
            factor_coverage={
                category: {
                    "status": "use only verified recent evidence from ranked items",
                    "references": [],
                }
                for category in (
                    "supply",
                    "demand",
                    "inventories",
                    "macro",
                    "geopolitical",
                )
            },
            data_quality=(
                "Underlying performance is third-party market data. Product structure is "
                "issuer-sourced. Spot and a complete dated futures curve are unavailable."
            ),
        )

    def _history(self, ticker: str, period: str) -> Any:
        try:
            history = self._ticker_factory(ticker).history(
                period=period,
                interval="1d",
                auto_adjust=False,
                actions=False,
                timeout=10,
                raise_errors=True,
            )
        except Exception as error:
            raise MarketDataError(f"Could not retrieve market context for {ticker}: {error}") from error
        if history is None or history.empty or not {"Close", "Volume"}.issubset(history.columns):
            raise MarketDataError(f"No usable market context found for {ticker}.")
        return history

    def _comparison(self, symbol: str, label: str) -> ComparisonReturn:
        try:
            history = self._history(symbol, "6mo")
            change = _period_change(history.dropna(subset=["Close"])["Close"], 30)
            return ComparisonReturn(symbol, label, change, "third-party market data")
        except MarketDataError:
            return ComparisonReturn(symbol, label, None, "unavailable")


def _period_change(series: Any, sessions: int) -> Decimal | None:
    if len(series) <= sessions:
        return None
    latest = Decimal(str(series.iloc[-1]))
    earlier = Decimal(str(series.iloc[-(sessions + 1)]))
    if earlier == 0:
        return None
    return (latest / earlier - Decimal("1")) * Decimal("100")


def comparison_symbols(ticker: str) -> tuple[tuple[str, str], ...]:
    if asset_profile_for(ticker).asset_type is AssetType.COMMODITY_ETP:
        return (("BZ=F", "primary Brent futures benchmark"), ("CL=F", "WTI oil benchmark"))
    sector = {"SPCX": ("RKLB", "public space-sector peer"), "NVDA": ("SOXX", "semiconductor ETF")}
    return (("SPY", "broad US market"), sector.get(ticker, ("SPY", "broad US market")))


def peer_symbol(ticker: str) -> str | None:
    return {"SPCX": "RKLB", "NVDA": "AMD"}.get(ticker)


def _commodity_product_context(ticker: str) -> CommodityProductContext:
    if ticker != "BRNT.L":
        raise MarketDataError(f"No verified product metadata exists for {ticker}.")
    return CommodityProductContext(
        ticker=ticker,
        product_name="WisdomTree Brent Crude Oil",
        structure="exchange-traded commodity; debt security",
        benchmark="Bloomberg Commodity Brent Crude Subindex 4W Total Return",
        benchmark_code="BCOMCO4T",
        management_fee_percent=Decimal("0.49"),
        base_currency="USD",
        trading_currency="USD",
        currency_hedged=False,
        replication_method="synthetic - fully funded collateralised swap",
        instruments="collateralised swaps referencing Brent crude futures",
        roll_methodology=(
            "Tracks a Brent futures index with contracts rolled on the index's "
            "pre-determined schedule; the product does not hold physical oil."
        ),
        issuer="WisdomTree Commodity Securities Limited",
        collateral_structure=(
            "Swap obligations are protected by collateral marked to market daily and held "
            "in segregated accounts at The Bank of New York Mellon."
        ),
        counterparty_note=(
            "The product relies on swap counterparties and relevant service providers; "
            "counterparty availability and performance remain product risks."
        ),
        tracking_difference=None,
        source=(
            "WisdomTree product page and issuer factsheet: "
            "https://www.wisdomtree.eu/es-es/products/ucits-etfs-unleveraged-etps/"
            "commodities/wisdomtree-brent-crude-oil"
        ),
        source_as_of=date(2026, 6, 5),
    )


def _fundamental_metric(
    name: str,
    value: object,
    unit: str,
    as_of: datetime,
    *,
    currency: str | None,
) -> FundamentalMetric:
    try:
        number = Decimal(str(value)) if value is not None else None
    except Exception:
        number = None
    if number is None:
        return FundamentalMetric(
            name,
            None,
            unit,
            "unavailable",
            as_of,
            "not supplied",
            source="Yahoo Finance/yfinance third-party snapshot",
            currency=currency,
            calculation_method="not available",
        )
    if name == "P/E" and number <= 0:
        return FundamentalMetric(
            name,
            number,
            unit,
            "not meaningful",
            as_of,
            "non-positive earnings",
            source="Yahoo Finance/yfinance third-party snapshot",
            currency=currency,
            calculation_method="provider-supplied; not independently reproduced",
        )
    return FundamentalMetric(
        name,
        number,
        unit,
        "third-party reported or estimated",
        as_of,
        "verify against the latest official filing",
        source="Yahoo Finance/yfinance third-party snapshot",
        currency=currency,
        calculation_method="provider-supplied; not independently reproduced",
        decision_grade=False,
        official_source=False,
    )
