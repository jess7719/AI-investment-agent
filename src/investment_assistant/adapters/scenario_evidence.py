"""Read saved first-party evidence, not generated reports, user profiles or private notes."""

import json
from datetime import datetime, timezone
from pathlib import Path

from investment_assistant.domain.analogues import AnalogueSearchResult
from investment_assistant.domain.regime import MarketRegimeSnapshot
from investment_assistant.domain.scenarios import ScenarioEvidencePackage
from investment_assistant.services.context_artifacts import ContextArtifactStore
from investment_assistant.services.historical_context import HistoricalContextService
from investment_assistant.services.news_service import TICKER_PATTERN
from investment_assistant.services.scenario_evidence import ScenarioEvidenceBuilder, timestamp


class LocalScenarioEvidenceSource:
    def __init__(self, root, reports_root=None, *, raw_evidence=None):
        self.root = Path(root)
        self.reports_root = Path(reports_root) if reports_root else None
        self.raw_evidence = raw_evidence

    def load(self, ticker, cutoff):
        if not TICKER_PATTERN.fullmatch(ticker):
            raise ValueError("Invalid scenario asset")
        raw, origins, exclusions = self.raw_evidence, [], []
        input_path = self.root / "scenario_inputs" / f"{ticker}.json"
        if raw is None and input_path.exists():
            try:
                payload = json.loads(input_path.read_text(encoding="utf-8"))
                if payload.get("schema_version") == "scenario-evidence-v1":
                    supplied = ScenarioEvidencePackage.model_validate(payload)
                    if supplied.asset.ticker != ticker or supplied.evidence_cutoff > cutoff:
                        raise ValueError("Input identity or cutoff mismatch")
                    return supplied
                raw = payload
                origins.append(str(input_path.resolve()))
            except (ValueError, OSError, AttributeError):
                exclusions.append("Saved scenario inputs were invalid and were excluded")
        if raw is None and self.reports_root:
            raw, origin = self._research_package(ticker, cutoff)
            if origin:
                origins.append(origin)
        if raw and (raw.get("scope") or {}).get("ticker", ticker) != ticker:
            raw = None
            exclusions.append("Research evidence for a different asset was excluded")
        store = ContextArtifactStore(self.root)
        regime = store._latest(self.root.glob("regimes/????-??-??.json"),
                               MarketRegimeSnapshot, cutoff.date())
        analogues = store._latest(self.root.glob(f"analogues/????-??-??/{ticker}.json"),
                                  AnalogueSearchResult, cutoff.date())
        cases = ()
        if analogues:
            if regime is None or regime.as_of < analogues.current.regime.as_of:
                regime = analogues.current.regime
            context = HistoricalContextService(self.root)
            cases = tuple(context.build_case(analogues, match) for match in analogues.matches)
            origins.append(f"analogues/{analogues.as_of}/{ticker}.json")
        return ScenarioEvidenceBuilder().build(ticker, cutoff, raw_evidence=raw, regime=regime,
            historical_cases=cases, source_artifacts=origins, exclusions=exclusions)

    def _research_package(self, ticker, cutoff):
        from investment_assistant.services.evidence_packages import EvidencePackageStore

        paths = list(self.reports_root.glob(f"????-??-??/evidence/{ticker}-deep-evidence*.json"))
        paths += list(self.reports_root.glob(
            f"web-evidence/*/????-??-??/evidence/{ticker}-deep-evidence*.json"))
        candidates = []
        for path in paths:
            try:
                package = EvidencePackageStore(self.reports_root).load(path)
                if package.evidence_timestamp <= cutoff and package.prepared.research.ticker == ticker:
                    candidates.append(package)
            except (ValueError, OSError):
                continue
        if not candidates:
            return None, None
        latest = max(candidates, key=lambda p: p.evidence_timestamp)
        return latest.prepared.research.evidence, str(latest.path)


def refresh_public_scenario_inputs(ticker, root, *, market=None, news=None):
    """Optional free public-data refresh. No reasoner, profile, notes or subscriber adapter."""
    from dataclasses import asdict

    from investment_assistant.adapters.news.google_news_rss import GoogleNewsRSSAdapter
    from investment_assistant.adapters.yfinance_market_data import YFinanceMarketDataAdapter
    from investment_assistant.services.historical_context import atomic_json
    from investment_assistant.services.news_service import NewsService
    from investment_assistant.services.research_service import _fundamental_evidence_context

    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError("Invalid scenario asset")
    market = market or YFinanceMarketDataAdapter()
    news = news or NewsService([GoogleNewsRSSAdapter()])
    raw = {"scope": {"ticker": ticker}, "ranked_information_items": []}
    try:
        snapshot = market.get_stock_snapshot(ticker)
        raw["market_data"] = {"latest_trading_date": str(snapshot.as_of),
            "five_day_percentage_change": str(snapshot.five_day_change_percent),
            "source": "Yahoo Finance daily market data"}
    except (OSError, ValueError, RuntimeError):
        pass
    try:
        fundamental = market.get_fundamental_context(ticker)
        raw["fundamental_and_valuation_context"] = _fundamental_evidence_context(fundamental)
    except (AttributeError, OSError, ValueError, RuntimeError):
        pass
    try:
        report = news.get_recent(ticker, fetch_limit=20, result_limit=20)
        for article in report.articles:
            row = asdict(article)
            raw["ranked_information_items"].append({
                "headline": article.headline, "source": article.publisher,
                "publication_time": article.published_at.isoformat(),
                "classification": article.content_classification.value,
                "evidence_depth": "source-provided summary" if article.summary else "headline only",
                "summary_supplied_by_source": row["summary"],
                "original_publisher_url": article.url,
            })
    except (OSError, ValueError, RuntimeError):
        pass
    path = Path(root) / "scenario_inputs" / f"{ticker}.json"
    raw["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(path, raw)
    return path


def load_scenario_input_file(path, ticker, cutoff):
    package = ScenarioEvidencePackage.model_validate_json(Path(path).read_text(encoding="utf-8"))
    if package.asset.ticker != ticker or package.evidence_cutoff > cutoff:
        raise ValueError("Scenario evidence file has a mismatched asset or future cutoff")
    # The source cutoff remains explicit; do not relabel older inputs as fresh observations.
    timestamp(package.evidence_cutoff)
    return package
