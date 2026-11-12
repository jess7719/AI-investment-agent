"""Offline fixtures for chronology, evidence, outcomes, presentation and shared caching."""

import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from investment_assistant import cli
from investment_assistant.adapters.news.google_news_rss import GoogleNewsRSSAdapter
from investment_assistant.adapters.news.historical_metadata import PublicHistoricalMetadataAdapter
from investment_assistant.domain.historical_context import HistoricalMarketContext, HistoricalSource
from investment_assistant.services.context_artifacts import ContextArtifactStore
from investment_assistant.services.context_cli import run_context_command
from investment_assistant.services.historical_analogues import (
    HistoricalAnalogueService, forward_outcomes,
)
from investment_assistant.services.historical_case_renderer import render_historical_case
from investment_assistant.services.historical_context import HistoricalContextService, SOURCE_ORDER
from investment_assistant.services.market_regime import ObservationSeries
from investment_assistant.web.app import create_app
from test_market_regime import FakeHistory, no_network, observations, trading_dates  # noqa: F401


@pytest.fixture(scope="module")
def result():
    return HistoricalAnalogueService(FakeHistory(target_size=5).service()).search(
        "TEST", trading_dates()[-1], 3)


def source(day, ref="official", category="tariffs_trade", quality="official_policy",
           headline="Government announces new import tariffs", summary=None, **kwargs):
    return HistoricalSource(
        reference=ref, headline=headline, publisher="Public source",
        published_at=datetime.combine(day, time(12), timezone.utc),
        url=f"https://example.gov/{ref}",
        summary=summary or "Import tariffs changed the costs faced by US automakers.",
        reviewed_summary=True, source_classification=quality, category=category,
        entities=("US automakers",), **kwargs)


class FixtureProvider:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_window(self, start, end):
        self.calls.append((start, end))
        # Deliberately violates endpoint filtering so service protections must work.
        return self.rows


def case_for(tmp_path, result, rows=()):
    service = HistoricalContextService(tmp_path, FixtureProvider(rows))
    return service.build_case(result, result.matches[0], retrieve=True)


def test_time_window_inclusive_boundaries_future_and_old_articles_excluded(tmp_path, result):
    day = result.matches[0].date
    rows = [source(day - timedelta(days=7), ref="start"),
            source(day, ref="end", category="inflation", headline="Producer price release"),
            source(day - timedelta(days=8), ref="old"),
            source(day + timedelta(days=1), ref="future")]
    provider = FixtureProvider(rows)
    case = HistoricalContextService(tmp_path, provider).build_case(
        result, result.matches[0], retrieve=True)
    assert provider.calls == [(day - timedelta(days=7), day)]
    assert {s.reference for s in case.evidence_refs} == {"start", "end"}
    assert all(e.known_by_analogue_date for e in case.context_before_date.main_events)
    serialized = case.context_before_date.model_dump_json()
    assert '"future"' not in serialized and '"old"' not in serialized
    invalid = case.context_before_date.model_dump()
    invalid["evidence_refs"] = (rows[-1],)
    invalid["source_count"] = 1
    with pytest.raises(ValidationError, match="outside historical"):
        HistoricalMarketContext.model_validate(invalid)


def test_timezone_normalisation_and_naive_dates_rejected(tmp_path, result):
    day = result.matches[0].date
    item = source(day).model_copy(update={"published_at": datetime.fromisoformat(
        f"{day + timedelta(days=1)}T01:00:00+02:00")})
    case = case_for(tmp_path, result, [item])
    assert case.context_before_date.main_events[0].event_date == day
    with pytest.raises(ValidationError, match="timezone"):
        HistoricalSource.model_validate({**item.model_dump(), "published_at": datetime.now()})


def test_clustering_deduplication_and_source_preference(tmp_path, result):
    day = result.matches[0].date
    rows = [source(day, "wire", quality="wire"),
            source(day, "official"),
            source(day, "other-trade", headline="New customs rates for imported auto parts",
                   quality="financial_news"),
            source(day, "prices", category="inflation", quality="official_data",
                   headline="Producer prices rise in release"),
            source(day, "tech", category="technology", quality="aggregation",
                   headline="Semiconductor shares react to new technology results")]
    case = case_for(tmp_path, result, rows)
    context = case.context_before_date
    assert len(context.event_clusters) == 3
    assert context.source_count == 4
    assert "wire" not in {s.reference for s in context.evidence_refs}
    order = [SOURCE_ORDER[s.source_classification] for s in context.evidence_refs]
    assert order == sorted(order)
    trade = next(c for c in context.event_clusters if c.category == "tariffs_trade")
    assert len(trade.evidence_refs) == 2
    assert all(f.kind == "FACT" for f in trade.facts)
    assert trade.interpretation.kind == "INTERPRETATION"
    assert trade.interpretation.certainty == "possible"
    assert len(trade.interpretation.steps) >= 3
    assert all(s.kind == "LESSON" for s in case.lessons)


def test_duplicate_url_and_similar_headline_removal(tmp_path, result):
    day = result.matches[0].date
    first = source(day, headline="Government announces large new import tariffs on cars today")
    second = source(day, "copy", quality="wire",
                    headline="Government announces large new import tariffs on cars today!")
    third = source(day, "syndicated").model_copy(update={"url": first.url + "?utm_source=x"})
    case = case_for(tmp_path, result, [second, third, first])
    assert case.context_before_date.source_count == 1


def test_unreviewed_metadata_cannot_create_narrative_or_mechanism(tmp_path, result):
    item = source(result.matches[0].date).model_copy(update={"reviewed_summary": False})
    case = case_for(tmp_path, result, [item])
    assert not case.context_before_date.event_clusters[0].facts
    assert not case.market_mechanisms
    assert "underlying claims have not been reviewed" in case.context_before_date.main_events[0].evidence.text
    assert "verified event explanation is not available" in render_historical_case(case)


def test_no_lookahead_in_narrative_quality_comparison_lessons_or_scoring(tmp_path, result):
    match = result.matches[0]
    provider = FixtureProvider([source(match.date), source(match.date + timedelta(days=1),
        "future", category="geopolitics", headline="Market collapse coming next month")])
    service = HistoricalContextService(tmp_path, provider)
    original = result.model_dump_json()
    altered = match.model_copy(update={"outcomes": tuple(o.model_copy(update={
        "benchmark_return": 9999, "raw_return": 8888, "benchmark_maximum_drawdown": -99})
        for o in match.outcomes)})
    with patch("investment_assistant.services.historical_analogues.similarity",
               side_effect=AssertionError("context must not score")):
        first = service.build_case(result, match, retrieve=True)
        second = service.build_case(result, altered, retrieve=True)
    assert first.model_dump(exclude={"historical_outcomes"}) == second.model_dump(
        exclude={"historical_outcomes"})
    assert result.model_dump_json() == original
    assert first.similarity_score == match.similarity
    assert "Market collapse" not in render_historical_case(first)


@pytest.mark.parametrize("window", [20, 60])
def test_forward_outcomes_exact_sessions_returns_excess_and_drawdown(window):
    days = trading_dates(65)
    benchmark = ObservationSeries(observations("SPY", [100, 120, 90] + [110] * 62, days))
    target = ObservationSeries(observations("TEST", [100] + [120] * 64, days))
    outcome = forward_outcomes(target, benchmark, days[0], days[-1], (window,))[0]
    assert outcome.start == days[0] and outcome.end == days[window]
    assert outcome.benchmark_return == pytest.approx(10)
    assert outcome.raw_return == pytest.approx(20)
    assert outcome.excess_return == pytest.approx(10)
    assert outcome.benchmark_maximum_drawdown == pytest.approx(-25)
    assert outcome.maximum_drawdown == 0


def test_unavailable_60d_never_extrapolated_and_missing_asset_sessions():
    days = trading_dates(40)
    benchmark = ObservationSeries(observations("SPY", [100 + i for i in range(40)], days))
    target = ObservationSeries(observations("TEST", [100] * 39, days[:10] + days[11:]))
    twenty, sixty = forward_outcomes(target, benchmark, days[0], days[-1], (20, 60))
    assert twenty.benchmark_return is not None and twenty.raw_return is None
    assert sixty.end is None and sixty.benchmark_return is None
    assert sixty.raw_return is None and sixty.benchmark_maximum_drawdown is None


def test_market_only_benchmark_labels_public_projection_and_internal_metadata(tmp_path, result):
    match = result.matches[0]
    changed = match.snapshot.regime.model_copy(update={"rates":
        match.snapshot.regime.rates.model_copy(update={"classification": "falling"})})
    match = match.model_copy(update={"snapshot": match.snapshot.model_copy(update={"regime": changed})})
    service = HistoricalContextService(tmp_path, FixtureProvider([source(match.date)]))
    case = service.build_case(result, match, retrieve=True)
    assert case.internal_differences_from_today
    assert case.context_before_date.evidence_quality
    assert case.market_mechanisms and case.lessons and case.similarities_to_today
    original = case.model_dump_json()
    view = json.dumps(case.beginner_view()).lower()
    text = render_historical_case(case).lower()
    for hidden in ("confidence", "differences", "evidence_quality", "source_classification",
                   "1-day", "5-day", "mechanisms", "lessons", "→",
                   "why was the market behaving", "what can we learn",
                   "based on observations available at the historical date"):
        assert hidden not in view and hidden not in text
    assert "historical us-market case, not a historical test case" in text
    assert "s&p 500 (spy price proxy) — 20 trading days" in text
    assert "test — 20 trading days" not in text
    assert set(case.historical_outcomes) == {"20d", "60d"}
    assert all(o.asset_return is None for o in case.historical_outcomes.values())
    assert case.model_dump_json() == original
    for shortened in (False, True):
        rendered = render_historical_case(case, shortened=shortened)
        headings = ("WHAT HAPPENED?", "WHAT IS SIMILAR TO TODAY?", "WHAT HAPPENED NEXT?", "Sources:")
        positions = [rendered.index(heading) for heading in headings]
        assert positions == sorted(positions)
        assert all(rendered.count(heading) == 1 for heading in headings)


def test_missing_outcome_is_unavailable_not_zero(tmp_path, result):
    match = result.matches[0].model_copy(update={"outcomes": ()})
    case = HistoricalContextService(tmp_path).build_case(result, match)
    assert all(not o.sufficient_benchmark_data for o in case.historical_outcomes.values())
    assert "20 trading days: unavailable" in render_historical_case(case)
    assert "60 trading days" not in render_historical_case(case)
    assert case.historical_outcomes["60d"].benchmark_return is None
    assert [o["trading_days"] for o in case.beginner_view()["outcomes"]] == [20]


def test_regime_similarities_ignore_unknowns_and_missing_current_narrative(tmp_path, result):
    case = case_for(tmp_path, result)
    assert case.similarities_to_today
    assert all(s.kind == "COMPARISON" for s in case.similarities_to_today)
    assert all("unknown" not in s.text and "inflation" not in s.text
               for s in case.similarities_to_today)
    assert not case.current_evidence_refs


def test_current_event_parallels_need_review_entities_content_and_current_cutoff(tmp_path, result):
    old = source(result.matches[0].date)
    today = source(result.as_of, "current")
    service = HistoricalContextService(tmp_path, FixtureProvider([old]))
    case = service.build_case(result, result.matches[0], retrieve=True,
                              current_evidence=[today, today.model_copy(update={
                                  "reference": "future", "published_at":
                                  today.published_at + timedelta(days=1)})])
    assert len(case.current_evidence_refs) == 1
    assert any("Both evidence windows" in s.text for s in case.similarities_to_today)
    unrelated = today.model_copy(update={"entities": ("Unrelated company",)})
    case = service.build_case(result, result.matches[0], retrieve=True,
                              current_evidence=[unrelated])
    assert not case.current_evidence_refs


def test_cache_shared_across_assets_current_dates_and_outcomes(tmp_path, result):
    day = result.matches[0].date
    provider = FixtureProvider([source(day)])
    service = HistoricalContextService(tmp_path, provider)
    first = service.build_case(result, result.matches[0], retrieve=True, persist=True)
    path = tmp_path / "historical_cases" / f"{day}.json"
    before = path.read_bytes()
    changed = result.model_copy(update={"ticker": "OTHER"})
    second = service.build_case(changed, result.matches[0], retrieve=True, persist=True)
    assert len(provider.calls) == 1
    assert first.context_before_date == second.context_before_date
    assert path.read_bytes() == before
    saved = json.loads(before)
    assert "current_as_of" not in saved and "historical_outcomes" not in saved
    comparison = service.save_case(first)
    assert comparison.parent.name == result.ticker
    assert "internal_differences_from_today" in json.loads(comparison.read_text())
    assert service.save_case(second) != comparison
    service.historical_context(day, retrieve=True, persist=True, refresh=True)
    assert len(provider.calls) == 2


def test_corrupt_wrong_version_and_wrong_window_cache_are_rebuilt(tmp_path, result):
    day = result.matches[0].date
    provider = FixtureProvider([source(day)])
    service = HistoricalContextService(tmp_path, provider)
    service.historical_context(day, retrieve=True, persist=True)
    path = tmp_path / "historical_cases" / f"{day}.json"
    for content in ("broken", path.read_text().replace("historical-context-v1", "old-v0")):
        path.write_text(content)
        service.historical_context(day, retrieve=True, persist=True)
    other = HistoricalContextService(tmp_path, provider, window_days=3)
    assert other.historical_context(day, retrieve=True).window_days == 3
    assert len(provider.calls) == 4


def test_empty_lookups_not_cached_and_can_retry(tmp_path, result):
    day = result.matches[0].date
    provider = FixtureProvider([])
    service = HistoricalContextService(tmp_path, provider)
    service.historical_context(day, retrieve=True, persist=True)
    provider.rows = [source(day)]
    assert service.historical_context(day, retrieve=True, persist=True).source_count == 1
    assert len(provider.calls) == 2


def test_metadata_adapter_only_requests_bounded_public_rss_never_article_or_paywall(tmp_path):
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        assert url.startswith("https://news.google.com/rss/search?")
        return b'''<rss><channel><item><title>US inflation update</title>
        <link>https://subscription.example/news</link>
        <pubDate>Fri, 28 Feb 2025 13:30:00 GMT</pubDate>
        <source url="https://bls.gov">BLS</source></item></channel></rss>'''
    adapter = PublicHistoricalMetadataAdapter(
        GoogleNewsRSSAdapter(fetcher=fetch), tmp_path / "empty.json")
    rows = adapter.fetch_window(date(2025, 2, 21), date(2025, 2, 28))
    assert len(calls) == 1 and len(rows) == 1
    query = parse_qs(urlsplit(calls[0]).query)["q"][0]
    assert "after:2025-02-20 before:2025-03-01" in query
    assert rows[0].summary is None and not rows[0].reviewed_summary
    assert rows[0].source_classification == "official_data"


def test_cli_commands_and_complete_case_no_openai_or_provider_when_cached(
        tmp_path, result, monkeypatch, capsys):
    store = ContextArtifactStore(tmp_path)
    store.save(result)
    service = HistoricalContextService(tmp_path, FixtureProvider([source(result.matches[0].date)]))
    assert run_context_command("analogue-case", result.as_of, ticker="TEST", store=store,
        analogue_date=result.matches[0].date, context_service=service) == 0
    text = capsys.readouterr().out
    for heading in ("WHAT HAPPENED?", "WHAT IS SIMILAR TO TODAY?",
                    "WHAT HAPPENED NEXT?", "Sources:", "No OpenAI request was made."):
        assert heading in text
    assert "confidence" not in text.lower()
    assert cli.build_parser().parse_args(["analogues", "SPCX", "--with-context"]).with_context
    parsed = cli.build_parser().parse_args(["analogue-case", "SPCX", "2025-02-28"])
    assert parsed.analogue_date == date(2025, 2, 28)
    # Exercise the actual dispatcher with a seeded cache and no live market/news calls.
    monkeypatch.setenv("INVESTMENT_RESEARCH_ROOT", str(tmp_path))
    service.historical_context(result.matches[0].date, retrieve=True, persist=True)
    monkeypatch.setattr(sys, "argv", ["investment-assistant", "analogue-case", "TEST",
        str(result.matches[0].date), "--as-of", str(result.as_of)])
    assert cli.main() == 0


def test_with_context_command_saves_full_cases_and_short_readable_output(tmp_path, capsys):
    store = ContextArtifactStore(tmp_path)
    provider = FixtureProvider([])
    context = HistoricalContextService(tmp_path, provider)
    assert run_context_command("analogues", trading_dates()[-1], ticker="TEST", top=2,
        service=FakeHistory(target_size=5).service(), store=store, with_context=True,
        context_service=context) == 0
    text = capsys.readouterr().out
    assert text.count("Historical Case —") == 2
    assert len(list(tmp_path.glob("historical_case_comparisons/*/TEST/*.json"))) == 2
    assert "confidence" not in text.lower() and "5-day" not in text


def test_explicit_date_not_in_top_matches_reuses_existing_scoring(result):
    from investment_assistant.services.context_cli import _dated_match
    day = trading_dates()[100]
    original = result.model_dump_json()
    match = _dated_match(result, day, FakeHistory(target_size=5).service())
    assert match.date == day and match.snapshot.as_of == day
    assert {o.trading_days for o in match.outcomes} == {20, 60}
    assert all(o.raw_return is None for o in match.outcomes)
    assert result.model_dump_json() == original
    with pytest.raises(ValueError, match="precede"):
        _dated_match(result, result.as_of, FakeHistory().service())
    with pytest.raises(ValueError, match="no benchmark"):
        _dated_match(result, date(2023, 1, 7), FakeHistory().service())


def test_web_card_sections_internal_fields_hidden_and_get_read_only(tmp_path, result, monkeypatch):
    monkeypatch.setenv("INVESTMENT_RESEARCH_ROOT", str(tmp_path / "context"))
    store = ContextArtifactStore(tmp_path / "context")
    store.save(result)
    store.save(result.current.regime)
    hostile = source(result.matches[0].date, summary="<script>alert(1)</script>")
    service = HistoricalContextService(store.root, FixtureProvider([hostile]))
    service.historical_context(result.matches[0].date, retrieve=True, persist=True)
    before = {str(p): p.read_bytes() for p in store.root.rglob("*.json")}
    with TestClient(create_app(tmp_path)) as client:
        client.get("/onboarding")
        client.headers["x-csrf-token"] = client.app.state.csrf
        client.post("/api/onboarding", json={"action": "skip", "step": 0, "answers": {}})
        response = client.get("/research/TEST")
        assert response.status_code == 200
        assert response.text.count("Read historical case") == len(result.matches)
        assert "&lt;script&gt;" in response.text and "<script>alert(1)" not in response.text
        section = response.text.split('<section class="panel"><h2>Market Environment</h2>')[1]
        section = section.rsplit("</details>", 1)[0]
        for heading in ("WHAT HAPPENED?", "WHAT IS SIMILAR TO TODAY?", "WHAT HAPPENED NEXT?"):
            assert section.count(heading) == len(result.matches)
        for hidden in ("confidence", "What is different", "Differences", "5-day", "1-day",
                       "Why was the market behaving", "What can we learn", "→",
                       "based on observations available at the historical date"):
            assert hidden not in section
        public = client.get("/api/market-context/TEST").json()
        payload = json.dumps(public)
        for hidden in ("confidence", "internal_differences", "evidence_quality",
                       "source_classification", "mechanisms", "lessons"):
            assert hidden not in payload
    assert before == {str(p): p.read_bytes() for p in store.root.rglob("*.json")}
