"""Build the SPCX validation example from the latest saved evidence without network or AI."""

from datetime import datetime, timezone

from investment_assistant.adapters.scenario_evidence import LocalScenarioEvidenceSource
from investment_assistant.adapters.scenario_store import ScenarioArtifactStore
from investment_assistant.config import PROJECT_ROOT, contextual_data_root
from investment_assistant.services.scenario_reasoning import ScenarioReasoningService
from investment_assistant.services.scenario_renderer import render_scenarios


def main():
    root = contextual_data_root()
    cutoff = datetime.now(timezone.utc)
    package = LocalScenarioEvidenceSource(root, PROJECT_ROOT / "reports").load("SPCX", cutoff)
    result = ScenarioReasoningService().generate(package)
    path = ScenarioArtifactStore(root).save(result)
    output = PROJECT_ROOT / "docs" / "examples" / "scenarios"
    output.mkdir(parents=True, exist_ok=True)
    (output / "SPCX-outlook.txt").write_text(render_scenarios(result) + "\n", encoding="utf-8")
    (output / "SPCX-dry-run.txt").write_text(render_scenarios(result, dry_run=True) + "\n", encoding="utf-8")
    (output / "SPCX.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"Evidence cutoff: {cutoff.isoformat()}")
    print(f"Market snapshot: {package.market_regime.as_of if package.market_regime else 'unavailable'}")
    print(f"Saved {len(result.scenarios)} conditional scenarios: {path}")
    print(f"Historical cases: {len(package.historical_cases)}; company price records: "
          f"{len(package.current_market_data)}")
    for horizon, summary in result.historical_summaries.items():
        print(f"{horizon}: n={summary.sample_size}; independent={summary.independent_sample_size}; "
              f"median benchmark return={summary.median_return}; "
              f"dispersion={summary.outcome_dispersion}; influence={summary.influence}")
    print("No network access or OpenAI request was used.")


if __name__ == "__main__":
    main()
