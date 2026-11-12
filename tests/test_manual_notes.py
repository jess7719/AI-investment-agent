import json
from datetime import date
from pathlib import Path

from investment_assistant.adapters.manual_notes import (
    ManualNoteRepository,
    load_note_file,
    note_to_evidence,
)
from investment_assistant.domain.evidence import EvidenceDepth


def test_loads_stores_and_reloads_private_note(tmp_path: Path) -> None:
    source = tmp_path / "note.json"
    source.write_text(
        json.dumps(
            {
                "publisher": "FT",
                "headline": "SpaceX analysis",
                "url": "https://ft.com/example",
                "publication_date": "2026-07-14",
                "summary": "My own summary.",
                "key_facts": ["Fact one", "Fact two"],
                "why_it_matters": "It affects the thesis.",
            }
        ),
        encoding="utf-8",
    )
    note = load_note_file(source, "SPCX")
    repository = ManualNoteRepository(tmp_path / "notes")

    saved = repository.add(note)
    loaded = repository.load("SPCX")[0]
    evidence = note_to_evidence(loaded)

    assert saved.exists()
    assert loaded.publication_date == date(2026, 7, 14)
    assert loaded.key_facts == ("Fact one", "Fact two")
    assert evidence.evidence_depth is EvidenceDepth.USER_PROVIDED_NOTES
    assert "My own summary" in (evidence.content or "")
