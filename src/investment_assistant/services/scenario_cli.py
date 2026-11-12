"""Composition boundary for saved evidence, optional public refresh, scenarios and artifacts."""

from datetime import date, datetime, time, timezone

from investment_assistant.adapters.scenario_evidence import (
    LocalScenarioEvidenceSource, load_scenario_input_file, refresh_public_scenario_inputs,
)
from investment_assistant.adapters.scenario_store import ScenarioArtifactStore
from investment_assistant.config import PROJECT_ROOT, contextual_data_root
from investment_assistant.services.news_service import TICKER_PATTERN
from investment_assistant.services.scenario_reasoning import ScenarioReasoningService
from investment_assistant.services.scenario_renderer import render_scenarios


def scenario_cutoff(as_of=None):
    now = datetime.now(timezone.utc)
    if as_of is None:
        return now
    if as_of > now.date():
        raise ValueError("Scenario date cannot be in the future")
    return min(datetime.combine(as_of, time.max, timezone.utc), now)


def run_scenarios(ticker, *, dry_run=False, as_of=None, evidence_file=None,
                  refresh_evidence=False, source=None, store=None):
    ticker = ticker.strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError("Invalid scenario asset")
    cutoff = scenario_cutoff(as_of)
    root = store.root if store else contextual_data_root()
    if refresh_evidence:
        if as_of and as_of != date.today() or evidence_file:
            raise ValueError("Public refresh requires current-date evidence without an input file")
        refresh_public_scenario_inputs(ticker, root)
    source = source or LocalScenarioEvidenceSource(root, PROJECT_ROOT / "reports")
    package = load_scenario_input_file(evidence_file, ticker, cutoff) if evidence_file else \
        source.load(ticker, cutoff)
    result = ScenarioReasoningService().generate(package)
    path = (store or ScenarioArtifactStore(root)).save(result)
    print(render_scenarios(result, dry_run=dry_run))
    print(f"Saved structured scenarios and evidence: {path}")
    return 0
