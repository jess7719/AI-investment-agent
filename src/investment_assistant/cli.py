"""Small command-line interface for the project's first learning milestone."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from investment_assistant.adapters.csv_portfolio import CsvPortfolioRepository
from investment_assistant.adapters.content_cache import ContentCache
from investment_assistant.adapters.investor_profiles import (
    InvestorProfileError,
    InvestorProfileRepository,
    load_profile_file,
)
from investment_assistant.adapters.openai_reasoner import (
    MAX_OUTPUT_TOKENS,
    OpenAIResearchReasoner,
)
from investment_assistant.adapters.openai_news_report import (
    NEWS_REPORT_MAX_OUTPUT_TOKENS,
    OpenAIDailyNewsReasoner,
)
from investment_assistant.adapters.openai_deep_report import (
    MalformedDeepAPIResponse,
    OpenAIDeepResearchReasoner,
    configured_deep_max_output_tokens,
    estimate_deep_input_tokens,
)
from investment_assistant.adapters.manual_notes import (
    ManualNoteError,
    ManualNoteRepository,
    load_note_file,
    note_from_mapping,
)
from investment_assistant.adapters.public_article_content import PublicArticleContentAdapter
from investment_assistant.adapters.sec_filing_content import SECFilingContentAdapter
from investment_assistant.adapters.yfinance_market_data import (
    MarketDataError,
    YFinanceMarketDataAdapter,
)
from investment_assistant.adapters.news import (
    FTNewsAdapter,
    GDELTNewsAdapter,
    GoogleNewsRSSAdapter,
    InvestorRelationsNewsAdapter,
    SECEdgarNewsAdapter,
    UniversityNewsDatabaseAdapter,
)
from investment_assistant.config import PROJECT_ROOT, Settings
from investment_assistant.domain.deep_report import GeneratedDeepResearchReport
from investment_assistant.domain.models import StockMarketSnapshot
from investment_assistant.domain.profile import InvestorProfile
from investment_assistant.ports.news import InvalidTickerError, NewsUnavailableError
from investment_assistant.ports.research import MissingAPIKeyError, ResearchError
from investment_assistant.ports.research import ResearchTransportError
from investment_assistant.services.news_service import NewsReport, NewsService
from investment_assistant.services.research_service import ResearchService
from investment_assistant.services.daily_news_report_service import DailyNewsReportService
from investment_assistant.services.deep_report_service import (
    DeepReportService,
    RejectedDeepReportError,
    SavedReportValidationError,
)
from investment_assistant.services.analysis_attempts import (
    AmbiguousAttemptExistsError,
    AnalysisAttemptStore,
)
from investment_assistant.services.evidence_packages import (
    EvidencePackageError,
    EvidencePackageStore,
    SavedEvidencePackage,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal investment research assistant")
    subcommands = parser.add_subparsers(dest="command")
    from investment_assistant.single_company.cli import add_parser
    add_parser(subcommands)
    scenarios = subcommands.add_parser("scenarios", help="Conditional 20/60-session outlook; no AI")
    scenarios.add_argument("ticker")
    scenarios.add_argument("--dry-run", action="store_true")
    scenarios.add_argument("--as-of", type=date.fromisoformat)
    scenarios.add_argument("--evidence-file", type=Path)
    scenarios.add_argument("--refresh-evidence", action="store_true",
                           help="Refresh free public metadata before reasoning")
    for command in ("regime", "analogues", "analogue-case"):
        context_parser = subcommands.add_parser(command, help="Deterministic market context; no AI")
        context_parser.add_argument("--as-of", type=date.fromisoformat)
        context_parser.add_argument("--benchmark", default="SPY")
        if command in {"analogues", "analogue-case"}:
            context_parser.add_argument("ticker")
            context_parser.add_argument("--top", type=int, default=10)
            context_parser.add_argument("--refresh-context", action="store_true")
            if command == "analogues":
                context_parser.add_argument("--with-context", action="store_true")
            else:
                context_parser.add_argument("analogue_date", type=date.fromisoformat)
    web_parser = subcommands.add_parser("web", help="Open the guided local research workspace")
    web_parser.add_argument("--port", type=int, default=8000)

    portfolio_parser = subcommands.add_parser("show-portfolio", help="Read a local CSV portfolio")
    portfolio_parser.add_argument(
        "--file",
        type=Path,
        default=Path("data/portfolio.example.csv"),
        help="Path to the CSV file (default: data/portfolio.example.csv)",
    )
    subcommands.add_parser("doctor", help="Check configuration without showing secrets")
    stock_parser = subcommands.add_parser("stock", help="Show recent daily data for one ticker")
    stock_parser.add_argument("ticker", help="Yahoo Finance ticker, for example NVDA")
    news_parser = subcommands.add_parser("news", help="Show ranked recent news for one ticker")
    news_parser.add_argument("ticker", help="Ticker to search, for example NVDA")
    news_parser.add_argument(
        "--official-first",
        action="store_true",
        help="Use balanced ranking with extra weight for verified official sources",
    )
    analyse_parser = subcommands.add_parser(
        "analyse", help="Create an evidence-grounded AI research report for one ticker"
    )
    analyse_parser.add_argument("ticker", help="Ticker to analyse, for example SPCX or NVDA")
    analyse_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and size evidence without calling OpenAI or incurring API cost",
    )
    prepare_parser = subcommands.add_parser(
        "prepare-analysis",
        help="Collect and save an immutable evidence package without OpenAI",
    )
    prepare_parser.add_argument("ticker", help="Ticker to prepare, for example SPCX")
    prepare_parser.add_argument(
        "--deep",
        action="store_true",
        help="Prepare the compact deep-analysis evidence package",
    )
    prepare_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Collect fresh evidence into a new immutable package",
    )
    evidence_analysis_parser = subcommands.add_parser(
        "analyse-from-evidence",
        help="Make one OpenAI request using only a saved evidence package",
    )
    evidence_analysis_parser.add_argument("path", type=Path, help="Saved evidence-package path")
    evidence_analysis_parser.add_argument(
        "--acknowledge-possible-duplicate-charge",
        action="store_true",
        help="Allow one request despite a pending ambiguous attempt for the same evidence",
    )
    analyse_parser.add_argument(
        "--deep",
        action="store_true",
        help="Create the beginner-friendly deep report using enriched evidence",
    )
    news_report_parser = subcommands.add_parser(
        "news-report",
        help="Create a beginner-friendly event-based daily news report",
    )
    news_report_parser.add_argument("ticker", help="Ticker to analyse, for example SPCX")
    news_report_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare event evidence without calling OpenAI or incurring API cost",
    )
    news_report_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace today's existing news report; may incur another API charge",
    )
    analyse_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace today's existing report; may incur another API charge",
    )
    note_parser = subcommands.add_parser(
        "add-note", help="Store your own summary of a legitimately accessed article"
    )
    note_parser.add_argument("ticker", help="Related ticker, for example SPCX")
    note_parser.add_argument(
        "--file",
        type=Path,
        help="UTF-8 JSON note file; without this option the command prompts securely",
    )
    profile_parser = subcommands.add_parser(
        "profile",
        help="Create or edit a private local investor profile for one ticker",
    )
    profile_parser.add_argument("ticker", help="Ticker for this profile, for example SPCX")
    profile_parser.add_argument(
        "--file",
        type=Path,
        help="UTF-8 JSON fields to merge; values are never printed",
    )
    validation_parser = subcommands.add_parser(
        "validate-report",
        help="Validate and locally repair a saved structured deep response",
    )
    validation_parser.add_argument("path", type=Path, help="Path to a structured JSON response")
    recovery_parser = subcommands.add_parser(
        "recover-report",
        help="Recover a rejected compact response entirely offline",
    )
    recovery_parser.add_argument("path", type=Path, help="Path to a rejected JSON response")
    return parser


def configure_terminal_encoding() -> None:
    """Replace unsupported glyphs instead of crashing on legacy Windows terminals."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def print_stock_snapshot(snapshot: StockMarketSnapshot) -> None:
    """Print a compact, readable stock report."""

    print(f"Stock:              {snapshot.ticker}")
    print(f"Latest trading day: {snapshot.as_of.isoformat()}")
    print(f"Latest close:       {snapshot.latest_close:,.2f}")
    print(f"Previous close:     {snapshot.previous_close:,.2f}")
    print(f"Daily change:       {snapshot.daily_change_percent:+.2f}%")
    print(f"5-day change:       {snapshot.five_day_change_percent:+.2f}%")
    print(f"Volume:             {snapshot.volume:,}")


def build_news_service(settings: Settings | None = None) -> NewsService:
    """Enable only public sources; subscription placeholders stay disabled."""

    settings = settings or Settings.from_environment()
    return NewsService(
        [
            SECEdgarNewsAdapter(user_agent=settings.sec_user_agent),
            InvestorRelationsNewsAdapter(),
            GDELTNewsAdapter(),
            GoogleNewsRSSAdapter(),
            FTNewsAdapter(),
            UniversityNewsDatabaseAdapter(),
        ]
    )


def print_news_report(report: NewsReport) -> None:
    if not report.articles:
        print(f"No recent news found for {report.ticker}.")
    else:
        print(f"Recent news for {report.ticker} ({len(report.articles)} article(s))")
        print("Metadata sources: SEC EDGAR, verified company IR, GDELT, and Google News RSS")
        if report.official_first:
            print("Ranking mode: balanced, with extra weight for official sources")
        for number, article in enumerate(report.articles, 1):
            print()
            print(f"{number}. {article.headline}")
            print(f"   Publisher:   {article.publisher}")
            print(f"   Published:   {article.published_at.isoformat()}")
            print(f"   Classification: {article.content_classification.value}")
            print(
                f"   Scores:      relevance {article.relevance_score}/100, "
                f"importance {article.importance_score}/100, "
                f"source confidence {article.source_confidence_score}/100, "
                f"recency {article.recency_score}/100"
            )
            print(f"   Balanced ranking score: {article.ranking_score:.1f}/100")
            if article.filing_form:
                print(f"   SEC form:    {article.filing_form}")
            if article.filing_description:
                print(f"   Description: {article.filing_description}")
            if article.filing_items:
                print(f"   SEC items:   {', '.join(article.filing_items)}")
            if article.filing_form and article.filing_form.upper() in {"4", "4/A"}:
                if article.insider_transactions:
                    print("   Structured Form 4 transactions:")
                    for transaction in article.insider_transactions:
                        print(
                            "   - "
                            f"person={transaction.reporting_person or 'not provided'}; "
                            f"date={transaction.transaction_date or 'not provided'}; "
                            f"type={transaction.transaction_type}; "
                            f"shares={_format_number(transaction.shares)}; "
                            f"price={_format_price(transaction.price_per_share)}"
                        )
                else:
                    print("   Form 4 details: unavailable; no insider sentiment inferred")
            print(f"   URL:         {article.url}")
            print(f"   Summary:     {article.summary or 'Not provided by source'}")

    if report.warnings:
        print("\nSource warnings:")
        for warning in report.warnings:
            print(f"- {warning}")
    if report.unavailable_sources:
        print("\nDisabled subscription sources:")
        for source in report.unavailable_sources:
            print(f"- {source}")


def main() -> int:
    configure_terminal_encoding()
    args = build_parser().parse_args()

    if args.command == "company":
        from investment_assistant.single_company.cli import run
        return run(args)

    if args.command == "scenarios":
        from investment_assistant.services.scenario_cli import run_scenarios

        try:
            return run_scenarios(args.ticker, dry_run=args.dry_run, as_of=args.as_of,
                                 evidence_file=args.evidence_file,
                                 refresh_evidence=args.refresh_evidence)
        except (ValueError, OSError):
            print("Could not build scenarios. Check ticker, dates and saved evidence.", file=sys.stderr)
            return 1

    if args.command in {"regime", "analogues", "analogue-case"}:
        from investment_assistant.services.context_cli import run_context_command

        try:
            return run_context_command(args.command, args.as_of, args.benchmark,
                                       getattr(args, "ticker", None), getattr(args, "top", 10),
                                       with_context=getattr(args, "with_context", False),
                                       analogue_date=getattr(args, "analogue_date", None),
                                       refresh_context=getattr(args, "refresh_context", False))
        except (ValueError, OSError):
            print("Could not build market context. Check ticker, date, options and storage.",
                  file=sys.stderr)
            return 1

    if args.command == "web":
        try:
            import uvicorn
            from investment_assistant.web.app import create_app
        except ImportError:
            print('Install web dependencies: python -m pip install -e ".[web]"', file=sys.stderr)
            return 1
        print(f"Research workspace: http://localhost:{args.port}")
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)
        return 0

    if args.command in (None, "show-portfolio"):
        portfolio_file = getattr(args, "file", Path("data/portfolio.example.csv"))
        holdings = CsvPortfolioRepository(portfolio_file).list_holdings()
        print(f"Loaded {len(holdings)} holding(s):")
        for holding in holdings:
            print(
                f"- {holding.exchange}:{holding.symbol}: {holding.quantity} shares of "
                f"{holding.company} "
                f"at average cost {holding.average_cost} {holding.currency}"
            )
        return 0

    if args.command == "stock":
        try:
            snapshot = YFinanceMarketDataAdapter().get_stock_snapshot(args.ticker)
        except MarketDataError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        print_stock_snapshot(snapshot)
        return 0

    if args.command == "news":
        try:
            report = build_news_service().get_recent(
                args.ticker,
                official_first=args.official_first,
            )
        except (InvalidTickerError, NewsUnavailableError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        print_news_report(report)
        return 0

    if args.command == "analyse":
        return run_analysis(
            args.ticker,
            dry_run=args.dry_run,
            force=args.force,
            deep=args.deep,
        )

    if args.command == "prepare-analysis":
        return run_prepare_analysis(args.ticker, deep=args.deep, refresh=args.refresh)

    if args.command == "analyse-from-evidence":
        return run_analyse_from_evidence(
            args.path,
            acknowledge_possible_duplicate_charge=(
                args.acknowledge_possible_duplicate_charge
            ),
        )

    if args.command == "news-report":
        return run_news_report(args.ticker, dry_run=args.dry_run, force=args.force)

    if args.command == "add-note":
        return run_add_note(args.ticker, args.file)

    if args.command == "profile":
        return run_profile(args.ticker, args.file)

    if args.command == "validate-report":
        return run_validate_report(args.path)

    if args.command == "recover-report":
        return run_recover_report(args.path)

    settings = Settings.from_environment()
    print(f"OpenAI model: {settings.openai_model}")
    for check, ready in settings.readiness().items():
        print(f"- {check}: {'yes' if ready else 'not yet'}")
    return 0


def run_analysis(
    ticker: str,
    *,
    dry_run: bool,
    force: bool,
    deep: bool = False,
) -> int:
    """Run or preview one bounded, evidence-only research request."""

    if deep:
        return run_deep_analysis(ticker, dry_run=dry_run, force=force)

    settings = Settings.from_environment()
    service = build_research_service(settings)
    try:
        path = service.report_path(ticker)
        if path.exists() and not force and not dry_run:
            print(f"Reusing today's existing report: {path}")
            print()
            print(path.read_text(encoding="utf-8"))
            return 0
        if not dry_run and settings.openai_api_key is None:
            raise MissingAPIKeyError(
                "OPENAI_API_KEY is missing or empty in the project-root .env file."
            )
        prepared = service.prepare(ticker)
        if dry_run:
            print(f"Dry run for {prepared.ticker}: no OpenAI request was made.")
            print("Evidence categories: market data, ranked news metadata, SEC filing metadata")
            print(
                f"Prepared items: 1 market snapshot, {prepared.news_count} news item(s), "
                f"{prepared.filing_count} filing item(s)"
            )
            print(
                f"Approximate request evidence: {prepared.character_count:,} characters "
                f"(~{prepared.approximate_tokens:,} tokens before instructions/schema overhead)"
            )
            print(
                f"Extracted article/filing content: "
                f"{prepared.extracted_content_characters:,} characters"
            )
            print("Evidence-depth labels:")
            for label, count in prepared.evidence_depth_counts:
                print(f"- {label}: {count}")
            print("Selected event clusters (up to 3 main developments):")
            selection = prepared.evidence_selection
            if selection and selection.main_clusters:
                for number, cluster in enumerate(selection.main_clusters, 1):
                    selected = cluster.selected
                    source = selected.article.publisher if selected else "unknown"
                    depth = selected.evidence_depth.value if selected else "unknown"
                    print(
                        f"{number}. {cluster.label} | source={source} | "
                        f"evidence={depth} | related items={len(cluster.items)}"
                    )
            else:
                print("- No qualifying main event cluster")
            print("Evidence sources prepared:")
            if selection:
                displayed = [
                    cluster.selected
                    for cluster in selection.main_clusters
                    if cluster.selected is not None
                ]
                displayed.extend(selection.secondary_items)
                for item in displayed:
                    print(
                        f"- {item.article.publisher}: {item.evidence_depth.value}; "
                        f"{item.extracted_characters:,} extracted characters; "
                        f"cache={'hit' if item.cache_hit else 'miss/not fetched'}"
                    )
            market_context = prepared.evidence.get("market_context")
            if isinstance(market_context, dict):
                comparisons = market_context.get("comparisons", [])
                print(
                    f"Extended market context: available; "
                    f"{len(comparisons) if isinstance(comparisons, list) else 0} "
                    "broad-market/peer comparison(s)"
                )
            else:
                print("Extended market context: unavailable")
            fundamentals = prepared.evidence.get("fundamental_and_valuation_context")
            if isinstance(fundamentals, dict):
                metrics = fundamentals.get("metrics", [])
                available_metrics = (
                    sum(
                        1
                        for metric in metrics
                        if isinstance(metric, dict) and metric.get("value") is not None
                    )
                    if isinstance(metrics, list)
                    else 0
                )
                quality = fundamentals.get("overall_quality", "unknown")
                print(
                    f"Fundamental/valuation context: {available_metrics} available metric(s); "
                    f"quality={quality}"
                )
            else:
                print("Fundamental/valuation context: unavailable")
            print(f"Configured model: {settings.openai_model}")
            print(f"Estimated output tokens: up to {MAX_OUTPUT_TOKENS:,}")
            print(f"Daily report path: {path}")
            print(f"Existing daily report: {'yes' if path.exists() else 'no'}")
            print("API key value: not displayed")
            return 0

        reasoner = OpenAIResearchReasoner(
            api_key=settings.openai_api_key or "",
            model=settings.openai_model,
        )
        completed = service.generate_and_save(prepared, reasoner)
    except ResearchError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(completed.markdown)
    print(f"Saved report: {completed.path}")
    return 0


def run_deep_analysis(ticker: str, *, dry_run: bool, force: bool) -> int:
    """Run or preview the beginner-friendly deep research report."""

    settings = Settings.from_environment()
    research_service = build_research_service(settings)
    profiles = InvestorProfileRepository(PROJECT_ROOT / "profiles")
    deep_service = DeepReportService(PROJECT_ROOT / "reports", profiles)
    try:
        normalized = ticker.strip().upper()
        path = deep_service.report_path(normalized, date.today().isoformat())
        if path.exists() and not force and not dry_run:
            print(f"Reusing today's existing deep report: {path}")
            print()
            print(path.read_text(encoding="utf-8"))
            return 0
        if not dry_run and settings.openai_api_key is None:
            raise MissingAPIKeyError(
                "OPENAI_API_KEY is missing or empty in the project-root .env file."
            )
        package_store = EvidencePackageStore(PROJECT_ROOT / "reports")
        canonical_package = package_store.canonical_path(normalized, date.today())
        if not dry_run and canonical_package.exists():
            return run_analyse_from_evidence(
                canonical_package,
                acknowledge_possible_duplicate_charge=False,
            )
        research = research_service.prepare(ticker)
        prepared = deep_service.prepare(research)
        if dry_run:
            input_estimate = estimate_deep_input_tokens(prepared.request_json)
            dry_scope = research.evidence.get("scope")
            dry_asset_type = (
                dry_scope.get("asset_type") if isinstance(dry_scope, dict) else "equity"
            )
            context_label = (
                "commodity, futures-curve, product, and market context"
                if dry_asset_type == "commodity_etp"
                else "market context and fundamentals"
            )
            print(f"Deep-analysis dry run for {research.ticker}: no OpenAI request was made.")
            print(
                "Report sections: bottom line, market movement, main developments, "
                "biggest issue, current view, scenarios, guidance, thesis checks, gaps"
            )
            print(
                f"Prepared evidence: {research.news_count} news item(s), "
                f"{research.filing_count} filing item(s), and {context_label}"
            )
            print(
                f"Approximate request evidence: {prepared.character_count:,} characters "
                f"(~{input_estimate.evidence_tokens:,} tokens)"
            )
            print(
                f"Estimated full input tokens: ~{input_estimate.total_tokens:,} "
                f"(evidence {input_estimate.evidence_tokens:,} + instructions "
                f"{input_estimate.instruction_tokens:,} + structured schema "
                f"{input_estimate.schema_tokens:,})"
            )
            print(
                f"Extracted article/filing content: "
                f"{research.extracted_content_characters:,} characters"
            )
            print("Evidence-depth labels:")
            for label, count in research.evidence_depth_counts:
                print(f"- {label}: {count}")
            print("Selected event clusters and likely main developments (maximum 3):")
            selection = research.evidence_selection
            if selection and selection.main_clusters:
                for number, cluster in enumerate(selection.main_clusters, 1):
                    selected = cluster.selected
                    source = selected.article.publisher if selected else "unknown"
                    depth = selected.evidence_depth.value if selected else "unknown"
                    print(
                        f"{number}. {cluster.label} | source={source} | "
                        f"evidence={depth} | related items={len(cluster.items)}"
                    )
            else:
                print("- none; the report will not invent developments to fill space")
            print("Evidence sources that would be sent:")
            ranked_items = research.evidence.get("ranked_information_items", [])
            metadata_items = ranked_items if isinstance(ranked_items, list) else []
            for item, metadata in zip(
                research.prepared_items,
                metadata_items,
                strict=False,
            ):
                reference = (
                    metadata.get("reference", "unreferenced")
                    if isinstance(metadata, dict)
                    else "unreferenced"
                )
                role = (
                    metadata.get("selection_role", "unknown")
                    if isinstance(metadata, dict)
                    else "unknown"
                )
                print(
                    f"- {reference} {item.article.publisher} | role={role} | "
                    f"classification={item.article.content_classification.value} | "
                    f"evidence={item.evidence_depth.value} | "
                    f"extracted={item.extracted_characters:,} chars | "
                    f"cache={'hit' if item.cache_hit else 'miss/not downloaded'}"
                )
            market_context = research.evidence.get("market_context")
            if isinstance(market_context, dict):
                comparisons = market_context.get("comparisons", [])
                print(
                    f"Market context: available with "
                    f"{len(comparisons) if isinstance(comparisons, list) else 0} comparison(s)"
                )
            else:
                print("Market context: unavailable")
            scope = research.evidence.get("scope")
            asset_type = (
                scope.get("asset_type") if isinstance(scope, dict) else "equity"
            )
            commodity = research.evidence.get("commodity_etp_context")
            fundamentals = research.evidence.get("fundamental_and_valuation_context")
            if asset_type == "commodity_etp" and isinstance(commodity, dict):
                curve = commodity.get("futures_curve", [])
                product = commodity.get("product", {})
                comparisons = (
                    market_context.get("comparisons", [])
                    if isinstance(market_context, dict)
                    else []
                )
                print("Asset type: commodity_etp")
                print(
                    "Commodity context: Brent spot="
                    f"{commodity.get('spot_price') or 'unavailable'}; front month="
                    f"{commodity.get('front_month_price') or 'unavailable'}; "
                    f"curve={commodity.get('curve_structure', 'unavailable')}; "
                    f"roll yield={commodity.get('roll_yield_direction', 'unavailable')}"
                )
                print(
                    f"Futures curve points: {len(curve) if isinstance(curve, list) else 0}; "
                    "contango/backwardation explicitly considered"
                )
                print(
                    "Underlying Brent performance: "
                    f"5-day={commodity.get('five_day_change_percent', 'unavailable')}%; "
                    f"30-day={commodity.get('thirty_day_change_percent', 'unavailable')}%; "
                    f"90-day={commodity.get('ninety_day_change_percent', 'unavailable')}%"
                )
                factors = commodity.get("factor_coverage", {})
                if isinstance(factors, dict):
                    print("Verified recent commodity-factor coverage:")
                    for category in (
                        "supply",
                        "demand",
                        "inventories",
                        "macro",
                        "geopolitical",
                    ):
                        factor = factors.get(category, {})
                        status = (
                            factor.get("status", "unavailable")
                            if isinstance(factor, dict)
                            else "unavailable"
                        )
                        references = (
                            factor.get("references", [])
                            if isinstance(factor, dict)
                            else []
                        )
                        leads = (
                            factor.get(
                                "metadata_leads_excluded_from_verified_factors", []
                            )
                            if isinstance(factor, dict)
                            else []
                        )
                        reference_text = ", ".join(str(item) for item in references)
                        lead_text = ", ".join(str(item) for item in leads)
                        print(
                            f"- {category}: {status}"
                            f"{f'; references={reference_text}' if reference_text else ''}"
                            f"{f'; metadata leads only={lead_text}' if lead_text else ''}"
                        )
                if isinstance(product, dict):
                    print(
                        f"Product context: {product.get('structure', 'unavailable')}; "
                        f"benchmark={product.get('benchmark', 'unavailable')}; "
                        f"fee={product.get('management_fee_percent', 'unavailable')}%"
                    )
                labels = [
                    item.get("label")
                    for item in comparisons
                    if isinstance(item, dict)
                ]
                print(f"Primary comparisons: {', '.join(str(item) for item in labels)}")
                print(
                    "Liquidity: exchange volume is separate from spread, underlying-market, "
                    "and market-maker liquidity"
                )
                print("Company-equity fundamentals: not applicable; not an evidence gap")
            elif isinstance(fundamentals, dict):
                metrics = fundamentals.get("metrics", [])
                available = (
                    sum(
                        1
                        for metric in metrics
                        if isinstance(metric, dict) and metric.get("value") is not None
                    )
                    if isinstance(metrics, list)
                    else 0
                )
                print(
                    f"Fundamental context: {available} available metric(s); "
                    f"quality={fundamentals.get('overall_quality', 'unknown')}"
                )
            else:
                print("Fundamental context: unavailable")
            print(
                "Investor profile: "
                f"{'configured' if prepared.profile_configured else 'not configured'}; "
                f"{prepared.profile_field_count} supplied field(s); values not displayed"
            )
            print("Compact views: 1-2 weeks, 1-3 months, and long-term thesis")
            confidence_subject = (
                "commodity/product evidence"
                if asset_type == "commodity_etp"
                else "fundamentals"
            )
            print(
                "Confidence measures: data, catalyst interpretation, "
                f"{confidence_subject}, near-term forecast, and long-term thesis"
            )
            print("Key evidence limit: 5 items; target report length: under 3,500 tokens")
            print(f"Configured model: {settings.openai_model}")
            print(f"Estimated output tokens: up to {configured_deep_max_output_tokens():,}")
            print(f"Deep report path: {path}")
            print(f"Existing deep report: {'yes' if path.exists() else 'no'}")
            print("API key value: not displayed")
            return 0

        package = package_store.save(prepared)
        return run_analyse_from_evidence(
            package.path,
            acknowledge_possible_duplicate_charge=False,
        )
    except RejectedDeepReportError as error:
        _print_api_completion(
            error.generated,
            validation_status="rejected",
            artifact_path=error.rejected_path,
        )
        print(f"Validation error summary: {error.validation_path}")
        for issue in error.validation.fatal_issues:
            print(f"- Fatal: {issue.message}", file=sys.stderr)
        return 1
    except (ResearchError, InvestorProfileError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1



def run_prepare_analysis(ticker: str, *, deep: bool, refresh: bool) -> int:
    """Collect evidence and save it without constructing an OpenAI client."""

    if not deep:
        print("Error: prepare-analysis currently requires --deep.", file=sys.stderr)
        return 1
    settings = Settings.from_environment()
    try:
        research_service = build_research_service(settings)
        profiles = InvestorProfileRepository(PROJECT_ROOT / "profiles")
        deep_service = DeepReportService(PROJECT_ROOT / "reports", profiles)
        package_store = EvidencePackageStore(PROJECT_ROOT / "reports")
        canonical = package_store.canonical_path(ticker, date.today())
        if canonical.exists() and not refresh:
            package = package_store.load(canonical)
            package = SavedEvidencePackage(
                package.path,
                package.sha256,
                package.evidence_timestamp,
                package.prepared,
                True,
            )
        else:
            research = research_service.prepare(ticker)
            package = package_store.save(deep_service.prepare(research), refresh=refresh)
    except (ResearchError, InvestorProfileError, EvidencePackageError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print("OpenAI request completed: no")
    print(f"Evidence package: {package.path}")
    print(f"Evidence timestamp: {package.evidence_timestamp.isoformat()}")
    print(f"Evidence package SHA-256: {package.sha256}")
    print(f"Evidence package reused: {'yes' if package.reused else 'no'}")
    for component in _package_components(package.path):
        print(
            f"- {component.get('component')}: {component.get('status')}; "
            f"retrieved={component.get('retrieved_at')}; "
            f"stale={'yes' if component.get('stale') else 'no'}"
        )
    print("API key value: not displayed")
    return 0


def run_analyse_from_evidence(
    path: Path,
    *,
    acknowledge_possible_duplicate_charge: bool,
) -> int:
    """Use one immutable package without reconnecting to any evidence source."""

    settings = Settings.from_environment()
    package_store = EvidencePackageStore(PROJECT_ROOT / "reports")
    attempt_store = AnalysisAttemptStore(PROJECT_ROOT / "reports")
    deep_service = DeepReportService(PROJECT_ROOT / "reports")
    attempt = None
    try:
        if settings.openai_api_key is None:
            raise MissingAPIKeyError(
                "OPENAI_API_KEY is missing or empty in the project-root .env file."
            )
        package = package_store.load(path)
        report_date = package.prepared.research.report_date.isoformat()
        max_output_tokens = configured_deep_max_output_tokens()
        attempt = attempt_store.begin(
            ticker=package.prepared.research.ticker,
            report_date=report_date,
            evidence_package_path=package.path,
            evidence_package_hash=package.sha256,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
            max_output_tokens=max_output_tokens,
            acknowledge_possible_duplicate_charge=(
                acknowledge_possible_duplicate_charge
            ),
        )
        reasoner = OpenAIDeepResearchReasoner(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            max_output_tokens=max_output_tokens,
            timeout_seconds=settings.openai_timeout_seconds,
        )
        completed = deep_service.generate_and_save(package.prepared, reasoner)
    except AmbiguousAttemptExistsError as error:
        print(f"Error: {error}", file=sys.stderr)
        for pending_path in error.paths:
            print(f"Pending attempt: {pending_path}", file=sys.stderr)
        print("Use --acknowledge-possible-duplicate-charge only after checking usage.")
        return 1
    except ResearchTransportError as error:
        if attempt is not None and error.ambiguous_billing:
            pending_path = attempt_store.mark_ambiguous(
                attempt,
                exception_category=error.category,
                openai_request_id=error.request_id,
            )
            print(f"Error: {error}", file=sys.stderr)
            print(f"Pending attempt: {pending_path}")
            print("Billing status: unknown")
            print("Do not retry automatically.")
            print("Check the OpenAI Usage Dashboard before submitting another request.")
            return 1
        if attempt is not None:
            attempt_store.finish(attempt)
        print(f"Error: {error}", file=sys.stderr)
        print(f"Exception category: {error.category}")
        print("Automatic retry performed: no")
        return 1
    except RejectedDeepReportError as error:
        if attempt is not None:
            attempt_store.finish(attempt)
        _print_api_completion(
            error.generated,
            validation_status="rejected",
            artifact_path=error.rejected_path,
        )
        print(f"Validation error summary: {error.validation_path}")
        return 1
    except (ResearchError, EvidencePackageError, OSError, ValueError) as error:
        if attempt is not None:
            attempt_store.finish(attempt)
        print(f"Error: {error}", file=sys.stderr)
        return 1
    attempt_store.finish(attempt)
    print(completed.markdown)
    _print_api_completion(
        completed.generated,
        validation_status=completed.validation.status.value,
        artifact_path=completed.path,
    )
    print(f"Structured response: {completed.structured_path}")
    print(f"Evidence package preserved: {package.path}")
    return 0


def _package_components(path: Path) -> list[dict[str, object]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    components = value.get("source_retrievals", []) if isinstance(value, dict) else []
    return [item for item in components if isinstance(item, dict)]


def run_validate_report(path: Path) -> int:
    """Validate and render a stored response without creating an OpenAI client."""

    service = DeepReportService(PROJECT_ROOT / "reports")
    try:
        completed = service.validate_saved_response(path)
    except SavedReportValidationError as error:
        print("API request completed: no")
        print("Report validation: rejected")
        for issue in error.validation.fatal_issues:
            print(f"- Fatal: {issue.message}", file=sys.stderr)
        return 1
    print(completed.markdown)
    print("API request completed: no")
    print(f"Report validation: {completed.validation.status.value}")
    for issue in completed.validation.issues:
        print(f"- {issue.severity.value}: {issue.message}")
    print(f"Report path: {completed.path}")
    return 0


def run_recover_report(path: Path) -> int:
    """Recover a rejected compact response without constructing an OpenAI client."""

    service = DeepReportService(PROJECT_ROOT / "reports")
    try:
        completed = service.recover_rejected_response(path)
    except (SavedReportValidationError, OSError, ValueError) as error:
        print("API request completed: no")
        print("Offline recovery: rejected")
        if isinstance(error, SavedReportValidationError):
            for issue in error.validation.fatal_issues:
                print(f"- Fatal: {issue.message}", file=sys.stderr)
        else:
            print(f"- Fatal: {error}", file=sys.stderr)
        return 1
    print(completed.markdown)
    print("API request completed: no")
    print("Additional paid request: no")
    print(f"Report validation: {completed.validation.status.value}")
    for issue in completed.validation.issues:
        print(f"- {issue.severity.value}: {issue.message}")
    print(f"Recovered structured JSON: {completed.structured_path}")
    print(f"Recovered Markdown: {completed.path}")
    return 0


def build_research_service(settings: Settings) -> ResearchService:
    """Create the shared, cached evidence-collection pipeline."""

    cache = ContentCache(PROJECT_ROOT / ".cache" / "investment-assistant")
    return ResearchService(
        market_data=YFinanceMarketDataAdapter(),
        news=build_news_service(settings),
        reports_root=PROJECT_ROOT / "reports",
        sec_content=SECFilingContentAdapter(settings.sec_user_agent, cache),
        article_content=PublicArticleContentAdapter(
            "PersonalInvestmentAssistant/0.1 (personal research tool)", cache
        ),
        notes=ManualNoteRepository(PROJECT_ROOT / "notes"),
    )


def run_news_report(ticker: str, *, dry_run: bool, force: bool) -> int:
    """Run or preview a bounded daily news-report request."""

    settings = Settings.from_environment()
    research_service = build_research_service(settings)
    profiles = InvestorProfileRepository(PROJECT_ROOT / "profiles")
    report_service = DailyNewsReportService(PROJECT_ROOT / "reports", profiles)
    try:
        normalized = ticker.strip().upper()
        path = report_service.report_path(normalized, date.today().isoformat())
        if path.exists() and not force and not dry_run:
            print(f"Reusing today's existing daily news report: {path}")
            print()
            print(path.read_text(encoding="utf-8"))
            return 0
        if not dry_run and settings.openai_api_key is None:
            raise MissingAPIKeyError(
                "OPENAI_API_KEY is missing or empty in the project-root .env file."
            )
        research = research_service.prepare(ticker)
        prepared = report_service.prepare(research)
        if dry_run:
            print(f"Daily news-report dry run for {research.ticker}: no OpenAI request was made.")
            print("Report purpose: explain distinct daily events and their investment meaning")
            print(
                f"Prepared evidence: {research.news_count} news item(s), "
                f"{research.filing_count} filing item(s), and market data"
            )
            print(
                f"Approximate request evidence: {prepared.character_count:,} characters "
                f"(~{prepared.approximate_tokens:,} tokens before instructions/schema overhead)"
            )
            print(
                f"Extracted article/filing content: "
                f"{research.extracted_content_characters:,} characters"
            )
            print("Evidence-depth labels:")
            for label, count in research.evidence_depth_counts:
                print(f"- {label}: {count}")
            print("Selected event clusters (the only eligible main developments):")
            selection = research.evidence_selection
            if selection and selection.main_clusters:
                for number, cluster in enumerate(selection.main_clusters, 1):
                    selected = cluster.selected
                    source = selected.article.publisher if selected else "unknown"
                    depth = selected.evidence_depth.value if selected else "unknown"
                    print(
                        f"{number}. {cluster.label} | source={source} | "
                        f"evidence={depth} | related items={len(cluster.items)}"
                    )
            else:
                print("- No qualifying main event cluster")
            print("Noise candidates kept separate from main events:")
            if selection and selection.noise_items:
                for item in selection.noise_items:
                    print(f"- {item.article.headline} | evidence={item.evidence_depth.value}")
            else:
                print("- none")
            print(
                "Investor profile: "
                f"{'configured' if prepared.profile_configured else 'not configured'}; "
                f"{prepared.profile_field_count} supplied field(s); values not displayed"
            )
            print(f"Configured model: {settings.openai_model}")
            print(f"Estimated output tokens: up to {NEWS_REPORT_MAX_OUTPUT_TOKENS:,}")
            print(f"Daily news report path: {path}")
            print(f"Existing daily news report: {'yes' if path.exists() else 'no'}")
            print("API key value: not displayed")
            return 0

        reasoner = OpenAIDailyNewsReasoner(
            api_key=settings.openai_api_key or "",
            model=settings.openai_model,
        )
        completed = report_service.generate_and_save(prepared, reasoner)
    except (ResearchError, InvestorProfileError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(completed.markdown)
    print(f"Saved report: {completed.path}")
    return 0


def run_add_note(ticker: str, file: Path | None) -> int:
    repository = ManualNoteRepository(PROJECT_ROOT / "notes")
    try:
        if file:
            note = load_note_file(file, ticker)
        else:
            print("Article contents are not fetched. Enter only your own summary and notes.")
            value: dict[str, object] = {
                "ticker": ticker,
                "publisher": input("Publisher: ").strip(),
                "headline": input("Headline: ").strip(),
                "url": input("URL: ").strip(),
                "publication_date": input("Publication date (YYYY-MM-DD): ").strip(),
                "summary": getpass.getpass("Your summary (hidden): ").strip(),
                "key_facts": getpass.getpass("Key facts, separated by | (hidden): ").split("|"),
                "why_it_matters": getpass.getpass("Why it matters (hidden): ").strip(),
                "date_added": datetime.now(timezone.utc).isoformat(),
            }
            note = note_from_mapping(value)
        path = repository.add(note)
    except ManualNoteError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Stored private note metadata for {note.ticker}: {path}")
    print("Summary and key facts were not printed.")
    return 0


def run_profile(ticker: str, file: Path | None) -> int:
    """Create or edit a private profile without echoing its values."""

    repository = InvestorProfileRepository(PROJECT_ROOT / "profiles")
    try:
        existing = repository.load(ticker)
        if file:
            updates = load_profile_file(file)
        else:
            print("Profile values are hidden. Leave a field blank to keep its current value.")
            print("To clear a field, use --file with that JSON field set to null.")
            updates = _prompt_profile_updates()
        profile, path = repository.update(ticker, updates)
    except InvestorProfileError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    state = "updated" if existing.is_configured else "created"
    print(f"Private profile {state} for {profile.ticker}: {path}")
    print(
        f"Configured fields: {len(profile.configured_fields)}; "
        "confidential values were not displayed."
    )
    return 0


def _prompt_profile_updates() -> dict[str, object]:
    prompts = (
        ("already_owns_stock", "Already owns stock (yes/no)"),
        ("average_purchase_price", "Average purchase price"),
        ("amount_invested", "Amount invested"),
        ("current_position_value", "Current position value"),
        ("intended_additional_investment", "Intended additional investment"),
        ("intended_holding_period", "Intended holding period"),
        ("risk_tolerance", "Risk tolerance (low/medium/high)"),
        (
            "maximum_acceptable_loss",
            "Maximum acceptable loss (include amount or percentage unit)",
        ),
        ("maximum_portfolio_percentage", "Maximum portfolio percentage (0-100)"),
        ("investment_objective", "Investment objective"),
        ("reason_for_investing", "Reason for investing"),
        ("position_description", "Position description"),
        ("trading_preference", "Trading preference"),
        ("investment_currency", "Investment currency"),
    )
    updates: dict[str, object] = {}
    for field, label in prompts:
        while True:
            value = getpass.getpass(f"{label} (hidden, blank keeps current): ").strip()
            if not value:
                break
            try:
                updates[field] = _parse_profile_input(field, value)
                break
            except InvestorProfileError as error:
                print(f"Invalid {field}: {error}")
                print(f"Required format: {_profile_format(field)}")
    return updates


def _parse_profile_input(field: str, value: str) -> object:
    normalized: object = value
    if field == "already_owns_stock":
        answer = value.lower()
        if answer not in {"yes", "y", "no", "n"}:
            raise InvestorProfileError("enter yes or no.")
        normalized = answer in {"yes", "y"}
    elif field == "maximum_portfolio_percentage":
        normalized = value.removesuffix("%").strip()
        if not normalized:
            raise InvestorProfileError("enter a percentage from 0 to 100.")
    try:
        profile = InvestorProfile.model_validate({"ticker": "VALIDATION", field: normalized})
    except ValueError as error:
        raise InvestorProfileError(_profile_format(field)) from error
    return getattr(profile, field)


def _profile_format(field: str) -> str:
    return {
        "already_owns_stock": "yes or no",
        "average_purchase_price": "a non-negative number, for example 125.50",
        "amount_invested": "a non-negative number, for example 5000",
        "current_position_value": "a non-negative number, for example 4750.25",
        "intended_additional_investment": "a non-negative number, for example 1000",
        "intended_holding_period": "plain text, for example 5 years",
        "risk_tolerance": "low, medium, or high",
        "maximum_acceptable_loss": "text with a unit, for example 20% or GBP 1000",
        "maximum_portfolio_percentage": (
            "a number from 0 to 100, optionally followed by %, for example 10 or 10%"
        ),
        "investment_objective": "plain text, for example long-term growth",
        "reason_for_investing": "plain text explaining your investment reason",
        "position_description": "plain text, for example intentionally small starter position",
        "trading_preference": "plain text, for example avoid frequent short-term trading",
        "investment_currency": "a currency code or name, for example GBP",
    }[field]


def _format_number(value: object | None) -> str:
    if value is None:
        return "not provided"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _format_price(value: object | None) -> str:
    if value is None:
        return "not provided"
    return f"${value:,.4f}".rstrip("0").rstrip(".")


def _print_api_completion(
    generated: GeneratedDeepResearchReport | MalformedDeepAPIResponse,
    *,
    validation_status: str,
    artifact_path: Path,
) -> None:
    usage = generated.usage
    print("API request completed: yes")
    print(
        f"Input tokens: {usage.input_tokens if usage.input_tokens is not None else 'not provided'}"
    )
    print(
        "Cached input tokens: "
        f"{usage.cached_input_tokens if usage.cached_input_tokens is not None else 'not provided'}"
    )
    print(
        f"Output tokens: "
        f"{usage.output_tokens if usage.output_tokens is not None else 'not provided'}"
    )
    print(
        f"Total tokens: {usage.total_tokens if usage.total_tokens is not None else 'not provided'}"
    )
    print(f"Model: {generated.model}")
    print(f"Response ID: {generated.response_id or 'not provided'}")
    print(
        "Request timestamp: "
        f"{generated.request_timestamp.isoformat() if generated.request_timestamp else 'not provided'}"
    )
    print(f"Report validation: {validation_status}")
    label = "Rejected-response path" if validation_status == "rejected" else "Report path"
    print(f"{label}: {artifact_path}")


if __name__ == "__main__":
    raise SystemExit(main())
