"""Beginner prose and explicit internal dry-run evidence; no personal action recommendations."""

import json


def render_scenarios(result, *, dry_run=False):
    card = result.beginner_view()
    lines = [f"Outlook for {card['ticker']}", f"Evidence cutoff: {card['evidence_cutoff']}"]
    if card["market_regime_as_of"]:
        lines.append(f"Market snapshot: {card['market_regime_as_of']}")
    for horizon in card["horizons"]:
        lines += ["", horizon["horizon"][:-1] + "-DAY OUTLOOK"]
        for scenario in horizon["scenarios"]:
            lines += ["", scenario["label"], scenario["summary"]]
            if scenario["why"]:
                lines += ["Why?"] + ["- " + reason for reason in scenario["why"]]
            lines += ["What must remain true?" if scenario["name"] == "base" else
                      "What would need to happen?"]
            lines += ["- " + text for text in scenario["conditions"]]
            lines += ["What would change this view?"]
            lines += ["- " + text for text in scenario["invalidation_triggers"]]
    if card["limitations"]:
        lines += ["", "Evidence gaps:"] + ["- " + text for text in card["limitations"]]
    lines += ["", card["notice"]]
    if dry_run:
        lines += ["", "INTERNAL DRY-RUN EVIDENCE — not a probability assessment", json.dumps({
            "evidence_package": result.evidence_state.model_dump(mode="json"),
            "historical_summaries": {h: s.model_dump(mode="json") for h, s in
                                     result.historical_summaries.items()},
            "contradictory_evidence": [c.model_dump(mode="json") for c in result.contradictory_evidence],
            "scenario_support": [{"name": s.name, "horizon": s.horizon,
                "supporting_evidence": list(s.supporting_evidence),
                "historical_context": list(s.historical_context),
                "drivers": [d.model_dump(mode="json") for d in s.drivers]} for s in result.scenarios],
        }, ensure_ascii=False, indent=2)]
    lines += ["No OpenAI request was made."]
    return "\n".join(lines)
