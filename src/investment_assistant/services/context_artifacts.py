"""Private contextual artifacts and bounded read-only evidence-package integration."""

import json
import os
import tempfile
from datetime import date
from pathlib import Path

from investment_assistant.domain.analogues import AnalogueSearchResult
from investment_assistant.domain.assets import AssetType, asset_profile_for
from investment_assistant.domain.regime import MarketRegimeSnapshot
from investment_assistant.services.news_service import TICKER_PATTERN
from investment_assistant.services.historical_context import HistoricalContextService


class ContextArtifactStore:
    def __init__(self, root: Path):
        self.root = root

    def save(self, value: MarketRegimeSnapshot | AnalogueSearchResult) -> Path:
        if isinstance(value, MarketRegimeSnapshot):
            suffix = "" if value.benchmark == "SPY" else f"-{self._ticker(value.benchmark)}"
            path = self.root / "regimes" / f"{value.as_of}{suffix}.json"
        else:
            suffix = "" if value.benchmark == "SPY" else f"-{self._ticker(value.benchmark)}"
            path = (self.root / "analogues" / value.as_of.isoformat()
                    / f"{self._ticker(value.ticker)}{suffix}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             delete=False, suffix=".tmp") as stream:
                name = stream.name
                stream.write(value.model_dump_json(indent=2))
            os.replace(name, path)
        finally:
            if name and Path(name).exists():
                Path(name).unlink()
        return path

    @staticmethod
    def _ticker(value):
        if not TICKER_PATTERN.fullmatch(value):
            raise ValueError("Invalid ticker in artifact path")
        return value

    def _latest(self, paths, model, as_of):
        for path in sorted(paths, reverse=True):
            try:
                value = model.model_validate_json(path.read_text(encoding="utf-8"))
                if value.as_of <= as_of:
                    return value
            except (ValueError, OSError):
                continue
        return None

    def read_context(self, ticker, as_of: date, *, include_cases: bool = False):
        ticker = self._ticker(ticker.strip().upper())
        if asset_profile_for(ticker).asset_type != AssetType.EQUITY:
            return {"market_regime": None, "historical_analogues": None,
                    "context_status": "listed-equity context not applicable to this asset type"}
        regime = self._latest(self.root.glob("regimes/????-??-??.json"),
                              MarketRegimeSnapshot, as_of)
        analogues = self._latest(self.root.glob(f"analogues/????-??-??/{ticker}.json"),
                                 AnalogueSearchResult, as_of)
        compact_regime = None
        if regime:
            compact_regime = json.loads(regime.model_dump_json(exclude={
                key: {"evidence": {"__all__": {"observations"}}}
                for key in regime.classification
            }))
            compact_regime["age_calendar_days"] = (as_of - regime.as_of).days
        compact_analogues = None
        if analogues and analogues.ticker == ticker:
            compact_analogues = {
                "as_of": str(analogues.as_of), "benchmark": analogues.benchmark,
                "age_calendar_days": (as_of - analogues.as_of).days,
                "mode": analogues.mode, "asset_specific_status": analogues.asset_specific_status,
                "sample_size": analogues.sample_size, "sample_warning": analogues.sample_warning,
                "evidence_kind": analogues.evidence_kind, "notice": analogues.notice,
                "summaries": [s.model_dump() for s in analogues.summaries
                              if not include_cases or s.trading_days in (20, 60)],
                "matches": [{"date": str(m.date), "similarity": m.similarity,
                             "outcomes": [json.loads(o.model_dump_json(exclude={"evidence"}))
                                          for o in m.outcomes if o.trading_days in (20, 60)]}
                            for m in (analogues.matches if include_cases else analogues.matches[:3])],
            }
            if include_cases:
                context = HistoricalContextService(self.root)
                for match, saved in zip(compact_analogues["matches"], analogues.matches):
                    # GET remains read-only: cache/catalogue only, no remote retrieval or writes.
                    case = context.build_case(analogues, saved)
                    match["historical_case"] = case.beginner_view()
        if include_cases and compact_regime:
            # Backend rules/evidence also contain quality language: expose only display facts.
            compact_regime = {
                **{key: compact_regime[key] for key in ("as_of", "benchmark", "age_calendar_days")},
                **{key: {"classification": compact_regime[key]["classification"]}
                   for key in regime.classification},
            }
        return {"market_regime": compact_regime, "historical_analogues": compact_analogues,
                "context_status": "saved deterministic contextual evidence; read-only; "
                                  "no automatic change to investment advice",
                "context_notice": "Historical forward outcomes are observations, never "
                                  "guaranteed future returns. Check each artifact's as-of date. "
                                  "This phase provides context for future analysis only: do not "
                                  "change investment advice or scenario probabilities from it."}
