"""Readable context formatting; live providers are composed only at the CLI boundary."""

from datetime import date

from investment_assistant.adapters.yfinance_regime import YFinanceRegimeAdapter
from investment_assistant.config import RegimeConfig, contextual_data_root
from investment_assistant.domain.regime import CLASSIFICATIONS
from investment_assistant.services.context_artifacts import ContextArtifactStore
from investment_assistant.services.historical_analogues import HistoricalAnalogueService
from investment_assistant.services.market_regime import MarketRegimeService


def build_regime_service(benchmark="SPY"):
    adapter = YFinanceRegimeAdapter()
    benchmarks = tuple(dict.fromkeys((benchmark, "SPY", "QQQ")))
    return MarketRegimeService(adapter, adapter, adapter, config=RegimeConfig(benchmarks=benchmarks))


def print_regime(snapshot):
    print(f"Market regime as of {snapshot.as_of} (benchmark: {snapshot.benchmark})")
    for key in CLASSIFICATIONS:
        dimension = getattr(snapshot, key)
        print(f"{key.replace('_', ' ').capitalize()}: "
              f"{dimension.classification.replace('_', ' ').capitalize()}")
    print("\nEvidence:")
    for key in ("equity_trend", "volatility", "rates"):
        values = [f"{e.reference}={e.value:.2f} {e.unit} ({e.timestamp}; {e.status})"
                  for e in getattr(snapshot, key).evidence if e.value is not None]
        print(f"- {key.replace('_', ' ')}: " + ("; ".join(values) or "unavailable"))
    print("- Monetary policy, inflation and growth: unknown; no enabled official macro rules.")


def _percent(value):
    return "unavailable" if value is None else f"{value:+.2f}%"


def print_analogues(result):
    print(f"Historical analogues for {result.ticker} as of {result.as_of}")
    print(f"Mode: {result.mode}; asset-specific: {result.asset_specific_status}")
    print(f"Top matches (sample {result.sample_size}; {result.sample_warning}):")
    for match in result.matches:
        outcomes = {o.trading_days: o for o in match.outcomes}
        values = "; ".join(f"{w}-day benchmark: " + _percent(
            outcomes[w].benchmark_return if w in outcomes else None) for w in (20, 60))
        print(f"- {match.date}: similarity {match.similarity:.1f}/100; {values}")
    print(f"Important: {result.notice}")


def _dated_match(result, day, service):
    """Score an explicitly requested date using the existing fixed scoring function."""
    from datetime import timedelta
    from investment_assistant.domain.analogues import HistoricalAnalogue
    from investment_assistant.services.historical_analogues import forward_outcomes, similarity
    from investment_assistant.services.historical_features import HistoricalFeatureBuilder
    from investment_assistant.services.market_regime import ObservationSeries

    if day >= result.as_of:
        raise ValueError("Historical case must precede the current snapshot")
    histories = service.histories(day - timedelta(days=550), result.as_of)
    asset_mode = result.mode == "asset_specific"
    target = (ObservationSeries(service.market.market_history(
        result.ticker, day - timedelta(days=550), result.as_of)) if asset_mode
        else ObservationSeries())
    benchmark = histories.market[result.benchmark]
    if day not in benchmark.dates:
        raise ValueError("Requested historical date has no benchmark trading close")
    snapshot = HistoricalFeatureBuilder(histories, target, result.ticker, service.config).build(
        day, asset_mode)
    score, distance, penalty, coverage = similarity(
        result.current, snapshot, result.weights, result.scales,
        result.configuration.get("category_penalty", .10))
    return HistoricalAnalogue(
        date=day, similarity=score, feature_distance=distance, category_penalty=penalty,
        feature_coverage=coverage, snapshot=snapshot,
        outcomes=forward_outcomes(target, benchmark, day, result.as_of, (20, 60), asset_mode))


def run_context_command(command, as_of, benchmark="SPY", ticker=None, top=10,
                        service=None, store=None, *, with_context=False, analogue_date=None,
                        context_service=None, refresh_context=False):
    from investment_assistant.domain.analogues import AnalogueSearchResult
    from investment_assistant.services.historical_context import HistoricalContextService
    from investment_assistant.services.historical_case_renderer import render_historical_case
    from investment_assistant.services.news_service import TICKER_PATTERN

    as_of = as_of or date.today()
    if as_of > date.today():
        raise ValueError("As-of date cannot be in the future")
    benchmark = benchmark.strip().upper()
    if not TICKER_PATTERN.fullmatch(benchmark):
        raise ValueError("Invalid benchmark ticker")
    if ticker:
        ticker = ticker.strip().upper()
        if not TICKER_PATTERN.fullmatch(ticker):
            raise ValueError("Invalid asset ticker")
    if analogue_date and analogue_date >= as_of:
        raise ValueError("Historical case must precede the current snapshot")
    store = store or ContextArtifactStore(contextual_data_root())
    context_service = context_service or HistoricalContextService(store.root)
    if command == "regime":
        service = service or build_regime_service(benchmark)
        result = service.snapshot(as_of)
        print_regime(result)
        path = store.save(result)
    else:
        result = None
        if command == "analogue-case":
            suffix = "" if benchmark == "SPY" else f"-{benchmark}"
            result = store._latest(store.root.glob(f"analogues/????-??-??/{ticker}{suffix}.json"),
                                   AnalogueSearchResult, as_of)
        if result is None:
            service = service or build_regime_service(benchmark)
            result = HistoricalAnalogueService(service).search(ticker, as_of, top)
            store.save(result.current.regime)
            path = store.save(result)
        else:
            path = "existing saved analogue evidence"
        if command == "analogue-case":
            match = next((m for m in result.matches if m.date == analogue_date), None)
            if match is None:
                service = service or build_regime_service(benchmark)
                match = _dated_match(result, analogue_date, service)
            matches = (match,)
        else:
            print_analogues(result)
            matches = result.matches if with_context else ()
        for match in matches:
            case = context_service.build_case(result, match, retrieve=True, persist=True,
                                               refresh=refresh_context)
            case_path = context_service.save_case(case)
            print("\n" + render_historical_case(case, shortened=command != "analogue-case"))
            print(f"Full structured historical case: {case_path}")
    print(f"\nSaved calculations and evidence: {path}")
    print("No OpenAI request was made.")
    return 0
