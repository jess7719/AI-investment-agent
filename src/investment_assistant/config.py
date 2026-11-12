"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def contextual_data_root(project_root: Path = PROJECT_ROOT) -> Path:
    """Private artifacts; an explicit root never changes existing report/profile locations."""
    return Path(os.getenv("INVESTMENT_RESEARCH_ROOT") or project_root / "data" / "research")


@dataclass(frozen=True)
class RegimeConfig:
    benchmarks: tuple[str, ...] = ("SPY", "QQQ")
    volatility_symbol: str = "^VIX"
    rates_symbol: str = "^TNX"
    stale_after_days: int = 5
    trend_return_threshold: float = 1.0
    strong_trend_60d: float = 8.0
    trend_ma_threshold: float = 0.5
    trend_vote_threshold: float = 0.4
    maximum_gap_days: int = 7
    vix_boundaries: tuple[float, float, float] = (15.0, 25.0, 35.0)
    volatility_percentile_boundaries: tuple[float, float, float] = (20.0, 80.0, 95.0)
    realised_vol_boundaries: tuple[float, float, float] = (10.0, 20.0, 35.0)
    percentile_window: int = 252
    percentile_minimum: int = 60
    rates_short_change: float = 0.05
    rates_medium_change: float = 0.15
    risk_score_threshold: float = 0.25
    volatility_risk_scores: tuple[float, float, float, float] = (1.0, 0.0, -0.5, -1.0)

    def __post_init__(self):
        from math import isfinite

        if not self.benchmarks or len(set(self.benchmarks)) != len(self.benchmarks):
            raise ValueError("Benchmarks must be nonempty and unique")
        if self.stale_after_days < 0 or self.percentile_minimum < 2 or self.maximum_gap_days < 1:
            raise ValueError("Invalid evidence windows")
        if self.percentile_window < self.percentile_minimum:
            raise ValueError("Percentile window is too small")
        for boundaries in (self.vix_boundaries, self.volatility_percentile_boundaries,
                           self.realised_vol_boundaries):
            if (tuple(sorted(set(boundaries))) != boundaries or len(boundaries) != 3
                    or any(not isfinite(v) for v in boundaries)):
                raise ValueError("Regime boundaries must be strictly increasing")
        for value in (self.trend_return_threshold, self.strong_trend_60d,
                      self.trend_ma_threshold, self.rates_short_change,
                      self.rates_medium_change, self.risk_score_threshold,
                      self.trend_vote_threshold):
            if not isfinite(value) or value <= 0:
                raise ValueError("Rule thresholds must be positive")
        if (len(self.volatility_risk_scores) != 4 or
                any(not isfinite(v) or abs(v) > 1 for v in self.volatility_risk_scores)):
            raise ValueError("Volatility risk scores must be finite and within [-1, 1]")


def _analogue_weights() -> dict[str, float]:
    return {
        "broad_market_5d_return": 0.04, "broad_market_20d_return": 0.08,
        "broad_market_60d_return": 0.08, "volatility_level": 0.075,
        "volatility_percentile": 0.075, "10y_yield_level": 0.075,
        "10y_yield_20d_change": 0.075, "risk_sentiment_score": 0.15,
        "target_asset_20d_momentum": 0.075, "target_asset_60d_momentum": 0.075,
        "target_asset_realised_volatility": 0.10, "target_asset_volume_ratio": 0.10,
    }


@dataclass(frozen=True)
class AnalogueConfig:
    weights: dict[str, float] = field(default_factory=_analogue_weights)
    scales: dict[str, float] = field(default_factory=lambda: {
        "broad_market_5d_return": 5.0, "broad_market_20d_return": 10.0,
        "broad_market_60d_return": 20.0, "volatility_level": 20.0,
        "volatility_percentile": 100.0, "10y_yield_level": 3.0,
        "10y_yield_20d_change": 1.0, "risk_sentiment_score": 2.0,
        "target_asset_20d_momentum": 15.0, "target_asset_60d_momentum": 30.0,
        "target_asset_realised_volatility": 50.0, "target_asset_volume_ratio": 2.0,
    })
    category_penalty: float = 0.10
    minimum_feature_coverage: float = 0.60
    historical_feature_coverage: float = 0.80
    minimum_asset_sessions: int = 126
    minimum_asset_candidates: int = 5
    separation_sessions: int = 20
    history_years: int = 10
    outcome_windows: tuple[int, ...] = (1, 5, 20, 60)

    def __post_init__(self):
        from math import isfinite

        if not self.weights or any(not isfinite(v) or v < 0 for v in self.weights.values()):
            raise ValueError("Feature weights must be finite and nonnegative")
        if sum(self.weights.values()) <= 0:
            raise ValueError("At least one feature weight must be positive")
        if any(k not in self.scales or not isfinite(self.scales[k]) or self.scales[k] <= 0
               for k in self.weights):
            raise ValueError("Every feature requires a finite positive scale")
        if not 0 < self.minimum_feature_coverage <= 1:
            raise ValueError("Invalid candidate coverage")
        if not 0 < self.historical_feature_coverage <= 1:
            raise ValueError("Invalid historical coverage")
        if not isfinite(self.category_penalty) or self.category_penalty < 0:
            raise ValueError("Invalid category penalty")
        if (self.minimum_asset_sessions < 61 or self.minimum_asset_candidates < 1
                or self.separation_sessions < 0 or self.history_years < 1
                or not self.outcome_windows or any(w < 1 for w in self.outcome_windows)):
            raise ValueError("Invalid historical windows")


@dataclass(frozen=True, slots=True)
class Settings:
    """Values that can change between computers without changing source code."""

    openai_api_key: str | None
    openai_model: str
    financial_data_api_key: str | None
    sec_user_agent: str
    openai_timeout_seconds: float = 180.0

    @classmethod
    def from_environment(cls) -> "Settings":
        load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
            financial_data_api_key=os.getenv("FINANCIAL_DATA_API_KEY") or None,
            sec_user_agent=os.getenv(
                "SEC_USER_AGENT",
                "PersonalInvestmentAssistant/0.1 personal-metadata-research",
            ),
            openai_timeout_seconds=_positive_float(
                os.getenv("OPENAI_TIMEOUT_SECONDS"), default=180.0
            ),
        )

    def readiness(self) -> dict[str, bool]:
        """Return safe booleans; never expose secret values in diagnostics."""

        return {
            "OpenAI API key configured": self.openai_api_key is not None,
            "Financial data API key configured": self.financial_data_api_key is not None,
        }


def _positive_float(raw: str | None, *, default: float) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default
