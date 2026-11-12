"""Trailing features only; this module never reads forward outcomes."""

from investment_assistant.config import RegimeConfig
from investment_assistant.domain.analogues import HistoricalRegimeSnapshot
from investment_assistant.domain.regime import RegimeEvidence
from investment_assistant.services.market_regime import (
    ObservationSeries, classify_regime, feature_evidence,
)


class HistoricalFeatureBuilder:
    def __init__(self, histories, target=None, ticker=None, config=None):
        self.histories = histories
        self.target = target or ObservationSeries()
        self.ticker = ticker
        self.config = config or RegimeConfig()

    def build(self, as_of, include_asset=True):
        config = self.config
        regime = classify_regime(self.histories, as_of, config)
        broad = self.histories.market.get(config.benchmarks[0], ObservationSeries()).through(
            as_of, 61)
        vix = self.histories.volatility.through(as_of, config.percentile_window)
        rates = self.histories.rates.through(as_of, 21)
        specs = {
            "broad_market_5d_return": (broad, "return_5", config.benchmarks[0]),
            "broad_market_20d_return": (broad, "return_20", config.benchmarks[0]),
            "broad_market_60d_return": (broad, "return_60", config.benchmarks[0]),
            "volatility_level": (vix, "level", config.volatility_symbol),
            "volatility_percentile": (vix, "percentile", config.volatility_symbol),
            "10y_yield_level": (rates, "level", config.rates_symbol),
            "10y_yield_20d_change": (rates, "change_20", config.rates_symbol),
        }
        if include_asset:
            target = self.target.through(as_of, 61)
            specs.update({
                "target_asset_20d_momentum": (target, "return_20", self.ticker),
                "target_asset_60d_momentum": (target, "return_60", self.ticker),
                "target_asset_realised_volatility": (target, "realised_volatility", self.ticker),
                "target_asset_volume_ratio": (target, "volume_ratio", self.ticker),
            })
        evidence = {key: feature_evidence(rows, as_of, key, kind, config, symbol or "unavailable")
                    for key, (rows, kind, symbol) in specs.items()}
        risk_inputs = regime.risk_sentiment.evidence
        available = [e for e in risk_inputs if e.timestamp is not None]
        latest = max(available, key=lambda e: e.timestamp) if available else None
        risk = regime.risk_sentiment
        evidence["risk_sentiment_score"] = RegimeEvidence(
            reference="risk_sentiment_score", source="deterministic composite of cited market facts",
            series=",".join(config.benchmarks) + "," + config.volatility_symbol,
            timestamp=latest.timestamp if latest else None,
            retrieved_at=latest.retrieved_at if latest else None,
            value=risk.component_scores.get("total"), unit="score [-1, 1]",
            calculation=risk.rule, rule=risk.rule, confidence=risk.confidence,
            status="missing" if risk.classification == "unknown" else
                   "stale" if any(e.status == "stale" for e in available) else
                   "partial" if any(e.status in {"partial", "missing"} for e in risk_inputs)
                   else "available",
            observations=tuple({(o.series, o.date): o for e in risk_inputs
                                for o in e.observations}.values()),
        )
        # Stale/incomplete features are explicit nulls, never numeric zero or forward-filled.
        features = {key: e.value if e.status == "available" else None
                    for key, e in evidence.items()}
        return HistoricalRegimeSnapshot(as_of=as_of, asset=self.ticker if include_asset else None,
                                        regime=regime, features=features,
                                        feature_evidence=evidence)
