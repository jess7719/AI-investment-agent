"""Reproduce the three SPCX examples entirely offline from saved observations and metadata."""

from investment_assistant.config import PROJECT_ROOT
from investment_assistant.domain.analogues import AnalogueSearchResult
from investment_assistant.services.historical_case_renderer import render_historical_case
from investment_assistant.services.historical_context import HistoricalContextService


def main():
    root = PROJECT_ROOT / "data" / "research"
    result = AnalogueSearchResult.model_validate_json(
        (root / "analogues" / "2026-11-02" / "SPCX.json").read_text(encoding="utf-8"))
    context = HistoricalContextService(root)
    output = PROJECT_ROOT / "docs" / "examples" / "historical-cases"
    output.mkdir(parents=True, exist_ok=True)
    for match in result.matches[:3]:
        # Check the saved benchmark returns against their full observation windows.
        for outcome in match.outcomes:
            if outcome.trading_days not in (20, 60) or outcome.benchmark_return is None:
                continue
            evidence = next(e for e in outcome.evidence if e.reference == "benchmark_forward_return")
            rows = evidence.observations
            assert len(rows) == outcome.trading_days + 1
            assert rows[0].date == match.date and rows[-1].date == outcome.end
            assert abs((rows[-1].value / rows[0].value - 1) * 100
                       - outcome.benchmark_return) < 1e-9
            peak, drawdown = rows[0].value, 0.0
            for row in rows:
                peak = max(peak, row.value)
                drawdown = min(drawdown, (row.value / peak - 1) * 100)
            assert abs(drawdown - outcome.benchmark_maximum_drawdown) < 1e-9
        case = context.build_case(result, match, persist=True, refresh=True)
        context.save_case(case)
        text = render_historical_case(case)
        assert "confidence" not in text.lower()
        (output / f"{match.date}.txt").write_text(text + "\n", encoding="utf-8")
        (output / f"{match.date}.json").write_text(case.model_dump_json(indent=2), encoding="utf-8")
        print(f"{match.date}: {match.similarity:.1f}/100; "
              f"{case.context_before_date.source_count} sources; {output / str(match.date)}")
    print("No network access or OpenAI request is used by this script.")


if __name__ == "__main__":
    main()
