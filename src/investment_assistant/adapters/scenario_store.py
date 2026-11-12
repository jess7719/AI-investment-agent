"""Versioned, hash-checked scenario snapshots with reproducible deterministic validation."""

import hashlib
import json
from pathlib import Path

from investment_assistant.domain.scenarios import ScenarioSet
from investment_assistant.services.historical_context import atomic_json
from investment_assistant.services.news_service import TICKER_PATTERN
from investment_assistant.services.scenario_reasoning import ScenarioReasoningService


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class ScenarioArtifactStore:
    def __init__(self, root):
        self.root = Path(root)

    def save(self, result):
        ScenarioReasoningService().validate(result)
        ticker = result.asset.ticker
        if not TICKER_PATTERN.fullmatch(ticker):
            raise ValueError("Invalid scenario artifact ticker")
        path = self.root / "scenarios" / str(result.scenario_date) / f"{ticker}.json"
        body = result.model_dump(mode="json")
        envelope = {"artifact_kind": "scenario_set", "scenario_set": body,
                    "evidence_sha256": _digest(body["evidence_state"])}
        envelope["artifact_sha256"] = _digest(envelope)
        cutoff = result.evidence_cutoff.strftime("%Y%m%dT%H%M%S%fZ")
        version = path.parent / "versions" / f"{ticker}-{cutoff}-{envelope['artifact_sha256'][:12]}.json"
        if not version.exists():
            atomic_json(version, envelope)
        atomic_json(path, envelope)
        return path

    def load(self, path):
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        digest = envelope.pop("artifact_sha256", None)
        if envelope.get("artifact_kind") != "scenario_set" or digest != _digest(envelope):
            raise ValueError("Scenario artifact integrity check failed")
        body = envelope["scenario_set"]
        if envelope.get("evidence_sha256") != _digest(body["evidence_state"]):
            raise ValueError("Scenario evidence hash mismatch")
        result = ScenarioSet.model_validate(body)
        return ScenarioReasoningService().validate(result)

    def latest(self, ticker, cutoff):
        if not TICKER_PATTERN.fullmatch(ticker):
            raise ValueError("Invalid scenario asset")
        for path in sorted(self.root.glob(f"scenarios/????-??-??/{ticker}.json"), reverse=True):
            try:
                result = self.load(path)
                if result.evidence_cutoff <= cutoff:
                    return result
            except (ValueError, OSError, KeyError, TypeError):
                continue
        return None
