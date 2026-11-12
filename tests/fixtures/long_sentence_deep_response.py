"""Structured paid-response fixture reproducing the former long-sentence failure."""

from __future__ import annotations

from typing import Any

from tests.test_deep_report import deep_report_data


def long_sentence_response_envelope() -> dict[str, Any]:
    report = deep_report_data()
    words = (
        "The U.S. market price was 141.72999572753906 according to the supplied "
        "[Market Data] while the public source at https://example.com/research "
        "described the event"
    ).split()
    while len(words) < 62:
        words.append("carefully")
    report["conclusion"]["summary"][0] = " ".join(words) + "."
    return {
        "schema_version": 2,
        "artifact_status": "received",
        "approved_for_investment_use": False,
        "notice": "TEST FIXTURE - not approved for investment use",
        "ticker": "SPCX",
        "report_date": "2026-07-16",
        "request_timestamp": "2026-07-16T12:00:00+00:00",
        "response_id": "resp_fixture_long_sentence",
        "model": "gpt-test",
        "usage": {
            "input_tokens": 2100,
            "cached_input_tokens": 400,
            "output_tokens": 1600,
            "total_tokens": 3700,
        },
        "report": report,
        "validation_context": {
            "allowed_references": ["[Market Data]", "[Filing 1]"],
            "allowed_main_clusters": ["event-1"],
            "evidence_depth_by_reference": {"[Filing 1]": "official filing extract"},
            "configured_profile_fields": [],
            "evidence": {
                "market_data": {
                    "reference": "[Market Data]",
                    "latest_close": "141.72999572753906",
                    "previous_close": "139.25",
                    "daily_percentage_change": "1.7800",
                    "five_day_percentage_change": "3.25",
                    "trading_volume": 141729995,
                },
                "market_context": {
                    "thirty_day_change_percent": "8.4",
                    "ninety_day_change_percent": "12.2",
                },
                "fundamental_and_valuation_context": None,
                "ranked_information_items": [
                    {
                        "reference": "[Filing 1]",
                        "headline": "SpaceX reports a material event",
                        "content_extract": "The filing discloses a material event.",
                        "evidence_depth": "official filing extract",
                    }
                ],
                "event_selection": {
                    "main_event_clusters": [
                        {
                            "cluster_id": "event-1",
                            "selected_reference": "[Filing 1]",
                        }
                    ]
                },
            },
        },
        "validation": {"status": "received", "issues": []},
    }
