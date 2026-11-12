"""Collect bounded evidence, validate AI output, and save daily research reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from investment_assistant.adapters.yfinance_market_data import MarketDataError
from investment_assistant.adapters.manual_notes import ManualNoteRepository, note_to_evidence
from investment_assistant.adapters.public_article_content import PublicArticleContentAdapter
from investment_assistant.adapters.sec_filing_content import SECFilingContentAdapter
from investment_assistant.domain.assets import AssetType, asset_profile_for
from investment_assistant.domain.context import (
    CommodityETPContext,
    FundamentalContext,
    MarketContext,
)
from investment_assistant.domain.evidence import (
    EvidenceDepth,
    EvidenceSelection,
    ExtractedEvidence,
)
from investment_assistant.domain.models import StockMarketSnapshot
from investment_assistant.domain.news import ContentClassification, NewsArticle
from investment_assistant.domain.research import (
    EvidenceStatement,
    GeneratedResearchReport,
    ImportantInformation,
    validate_report_evidence,
)
from investment_assistant.ports.news import (
    InvalidTickerError,
    NewsUnavailableError,
)
from investment_assistant.ports.providers import SingleStockMarketDataProvider
from investment_assistant.ports.research import (
    MalformedResearchResponseError,
    ResearchDataUnavailableError,
    ResearchReasoner,
)
from investment_assistant.services.news_service import NewsService
from investment_assistant.services.news_service import TICKER_PATTERN
from investment_assistant.services.evidence_selection import select_evidence
from investment_assistant.config import contextual_data_root
from investment_assistant.services.context_artifacts import ContextArtifactStore

MAX_EVIDENCE_ITEMS = 8
MAX_HEADLINE_CHARS = 240
MAX_SUMMARY_CHARS = 400


@dataclass(frozen=True, slots=True)
class PreparedResearch:
    ticker: str
    report_date: date
    evidence: dict[str, object]
    evidence_json: str
    allowed_references: frozenset[str]
    news_count: int
    filing_count: int
    evidence_selection: EvidenceSelection | None = None
    evidence_depth_counts: tuple[tuple[str, int], ...] = ()
    extracted_content_characters: int = 0
    prepared_items: tuple[ExtractedEvidence, ...] = ()

    @property
    def character_count(self) -> int:
        return len(self.evidence_json)

    @property
    def approximate_tokens(self) -> int:
        return max(1, round(self.character_count / 4))


@dataclass(frozen=True, slots=True)
class CompletedResearch:
    generated: GeneratedResearchReport
    markdown: str
    path: Path


class ResearchService:
    def __init__(
        self,
        market_data: SingleStockMarketDataProvider,
        news: NewsService,
        reports_root: Path = Path("reports"),
        today: date | None = None,
        sec_content: SECFilingContentAdapter | None = None,
        article_content: PublicArticleContentAdapter | None = None,
        notes: ManualNoteRepository | None = None,
    ) -> None:
        self._market_data = market_data
        self._news = news
        self._reports_root = reports_root
        self._today = today
        self._sec_content = sec_content
        self._article_content = article_content
        self._notes = notes

    def report_path(self, ticker: str) -> Path:
        normalized = _normalize_ticker(ticker)
        report_date = self._today or date.today()
        return self._reports_root / report_date.isoformat() / f"{normalized}.md"

    def prepare(self, ticker: str) -> PreparedResearch:
        normalized = _normalize_ticker(ticker)
        try:
            market = self._market_data.get_stock_snapshot(normalized)
        except MarketDataError as error:
            raise ResearchDataUnavailableError(
                f"Market data unavailable for {normalized}: {error}"
            ) from error
        try:
            news_report = self._news.get_recent(
                normalized,
                fetch_limit=20,
                result_limit=MAX_EVIDENCE_ITEMS,
                official_first=True,
            )
        except (InvalidTickerError, NewsUnavailableError) as error:
            raise ResearchDataUnavailableError(
                f"News data unavailable for {normalized}: {error}"
            ) from error
        if not news_report.articles:
            raise ResearchDataUnavailableError(
                f"News data unavailable for {normalized}: no ranked items were found."
            )

        extracted = [self._enrich(article) for article in news_report.articles]
        if self._notes:
            extracted.extend(note_to_evidence(note) for note in self._notes.load(normalized))
        selection = select_evidence(extracted)
        selected_items = [
            cluster.selected for cluster in selection.main_clusters if cluster.selected is not None
        ]
        selected_items.extend(selection.noise_items)
        selected_item_ids = {id(item) for item in selected_items}
        selected_items.extend(
            item for item in selection.secondary_items if id(item) not in selected_item_ids
        )
        selected_items = selected_items[:MAX_EVIDENCE_ITEMS]
        asset_profile = asset_profile_for(normalized)
        market_context = self._optional_context("get_market_context", normalized)
        fundamentals = (
            self._optional_context("get_fundamental_context", normalized)
            if asset_profile.asset_type is AssetType.EQUITY
            else None
        )
        commodity_context = (
            self._optional_context("get_commodity_etp_context", normalized)
            if asset_profile.asset_type is AssetType.COMMODITY_ETP
            else None
        )
        evidence, allowed, news_count, filing_count = build_evidence_payload(
            market,
            selected_items,
            self._today or date.today(),
            market_context=market_context if isinstance(market_context, MarketContext) else None,
            fundamentals=(fundamentals if isinstance(fundamentals, FundamentalContext) else None),
            commodity_context=(
                commodity_context
                if isinstance(commodity_context, CommodityETPContext)
                else None
            ),
            selection=selection,
        )
        evidence.update(ContextArtifactStore(contextual_data_root(
            self._reports_root.parent)).read_context(normalized, self._today or date.today()))
        model_evidence = dict(evidence)
        model_evidence.pop("raw_numeric_evidence", None)
        evidence_json = json.dumps(model_evidence, ensure_ascii=False, separators=(",", ":"))
        return PreparedResearch(
            ticker=normalized,
            report_date=self._today or date.today(),
            evidence=evidence,
            evidence_json=evidence_json,
            allowed_references=frozenset(allowed),
            news_count=news_count,
            filing_count=filing_count,
            evidence_selection=selection,
            evidence_depth_counts=tuple(sorted(_depth_counts(selected_items).items())),
            extracted_content_characters=sum(item.extracted_characters for item in selected_items),
            prepared_items=tuple(selected_items),
        )

    def _enrich(self, article: NewsArticle) -> ExtractedEvidence:
        if (
            article.content_classification is ContentClassification.REGULATORY_FILING
            and self._sec_content
        ):
            return self._sec_content.enrich(article)
        if self._article_content:
            return self._article_content.enrich(article)
        if article.summary:
            return ExtractedEvidence(
                article,
                EvidenceDepth.SOURCE_PROVIDED_SUMMARY,
                article.summary[:MAX_SUMMARY_CHARS],
                article.url,
            )
        return ExtractedEvidence(article, EvidenceDepth.HEADLINE_ONLY, None, article.url)

    def _optional_context(self, method_name: str, ticker: str) -> object | None:
        method = getattr(self._market_data, method_name, None)
        if method is None:
            return None
        try:
            return method(ticker)
        except MarketDataError:
            return None

    def generate_and_save(
        self,
        prepared: PreparedResearch,
        reasoner: ResearchReasoner,
    ) -> CompletedResearch:
        generated = reasoner.generate(prepared.evidence_json)
        try:
            validate_report_evidence(
                generated.report,
                set(prepared.allowed_references),
            )
        except ValueError as error:
            raise MalformedResearchResponseError(
                f"OpenAI response failed evidence validation: {error}"
            ) from error
        markdown = render_markdown_report(prepared, generated)
        path = self.report_path(prepared.ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        return CompletedResearch(generated, markdown, path)


def build_evidence_payload(
    market: StockMarketSnapshot,
    articles: list[ExtractedEvidence] | list[NewsArticle],
    report_date: date | None = None,
    *,
    market_context: MarketContext | None = None,
    fundamentals: FundamentalContext | None = None,
    commodity_context: CommodityETPContext | None = None,
    selection: EvidenceSelection | None = None,
) -> tuple[dict[str, object], set[str], int, int]:
    items: list[dict[str, object]] = []
    news_number = 0
    filing_number = 0
    allowed = {"[Market Data]"}
    extracted_items = [
        item
        if isinstance(item, ExtractedEvidence)
        else ExtractedEvidence(item, EvidenceDepth.HEADLINE_ONLY, None, item.url)
        for item in articles
    ]
    main_item_ids = (
        {
            id(cluster.selected)
            for cluster in selection.main_clusters
            if cluster.selected is not None
        }
        if selection
        else {id(item) for item in extracted_items}
    )
    secondary_item_ids = {id(item) for item in selection.secondary_items} if selection else set()
    noise_item_ids = {id(item) for item in selection.noise_items} if selection else set()
    cluster_ids = (
        {
            id(item): cluster.cluster_id
            for cluster in selection.main_clusters
            for item in cluster.items
        }
        if selection
        else {}
    )
    reference_by_item_id: dict[int, str] = {}
    for evidence_item in extracted_items[:MAX_EVIDENCE_ITEMS]:
        article = evidence_item.article
        if article.content_classification is ContentClassification.REGULATORY_FILING:
            filing_number += 1
            reference = f"[Filing {filing_number}]"
            evidence_basis = (
                "structured Form 4 metadata" if article.insider_transactions else "filing metadata"
            )
        else:
            news_number += 1
            reference = f"[News {news_number}]"
            evidence_basis = "headline and metadata"
        allowed.add(reference)
        reference_by_item_id[id(evidence_item)] = reference
        selection_role = "main"
        if id(evidence_item) in noise_item_ids:
            selection_role = "noise"
        elif id(evidence_item) in secondary_item_ids:
            selection_role = "secondary"
        item: dict[str, object] = {
            "reference": reference,
            "headline": article.headline[:MAX_HEADLINE_CHARS],
            "source": article.publisher[:150],
            "publication_time": article.published_at.isoformat(),
            "classification": article.content_classification.value,
            "evidence_basis": evidence_basis,
            "evidence_depth": evidence_item.evidence_depth.value,
            "event_cluster_id": cluster_ids.get(id(evidence_item)),
            "selection_role": selection_role,
            "content_extract": (
                evidence_item.content
                if id(evidence_item) in main_item_ids
                else (evidence_item.content or "")[:500] or None
            ),
            "original_publisher_url": evidence_item.original_url,
            "access_note": evidence_item.access_note,
            "summary_supplied_by_source": (
                article.summary[:MAX_SUMMARY_CHARS] if article.summary else None
            ),
            "scores": {
                "relevance": article.relevance_score,
                "financial_importance": article.importance_score,
                "source_confidence": article.source_confidence_score,
                "recency": article.recency_score,
                "balanced_ranking": article.ranking_score,
            },
        }
        if article.filing_form:
            item["filing_metadata"] = {
                "form": article.filing_form,
                "description": article.filing_description,
                "items": list(article.filing_items),
                "official_url": evidence_item.official_url or article.url,
                "extracted_sections": [
                    {"heading": heading, "text": text}
                    for heading, text in evidence_item.filing_sections
                ]
                if id(evidence_item) in main_item_ids
                else [],
                "exhibit_descriptions": list(evidence_item.exhibit_descriptions[:5]),
                "material_event_summary": (
                    evidence_item.material_event_summary
                    if id(evidence_item) in main_item_ids
                    else None
                ),
            }
        if evidence_item.structured_ownership:
            row_limit = 6 if id(evidence_item) in main_item_ids else 2
            item["structured_ownership"] = list(evidence_item.structured_ownership[:row_limit])
        if article.insider_transactions:
            item["structured_form4_transactions"] = [
                {
                    "reporting_person": transaction.reporting_person,
                    "transaction_date": (
                        transaction.transaction_date.isoformat()
                        if transaction.transaction_date
                        else None
                    ),
                    "transaction_code": transaction.transaction_code,
                    "transaction_type": transaction.transaction_type,
                    "shares": _decimal_text(transaction.shares),
                    "price_per_share": _decimal_text(transaction.price_per_share),
                }
                for transaction in article.insider_transactions[:6]
            ]
        items.append(item)

    raw_market_data = {
        "latest_close": str(market.latest_close),
        "previous_close": str(market.previous_close),
        "daily_percentage_change": str(market.daily_change_percent),
        "five_day_percentage_change": str(market.five_day_change_percent),
        "trading_volume": market.volume,
    }
    raw_market_context = _json_safe(asdict(market_context)) if market_context else None
    raw_fundamentals = _json_safe(asdict(fundamentals)) if fundamentals else None
    decision_fundamentals = _fundamental_evidence_context(fundamentals)
    asset_profile = asset_profile_for(market.ticker)
    serialized_commodity = (
        _canonical_numeric_context(_json_safe(asdict(commodity_context)))
        if commodity_context
        else None
    )
    if isinstance(serialized_commodity, dict):
        serialized_commodity["factor_coverage"] = _commodity_factor_coverage(items)
    payload: dict[str, object] = {
        "scope": {
            "ticker": market.ticker,
            "asset_type": asset_profile.asset_type.value,
            "asset_name": asset_profile.name,
            "report_date": (report_date or date.today()).isoformat(),
            "limitations": [
                "Only bounded extracts and explicitly labelled metadata were supplied.",
                "No complete article body or complete SEC filing was supplied.",
                "Headlines and metadata do not prove why the market price moved.",
            ],
        },
        "market_data": {
            "reference": "[Market Data]",
            "latest_trading_date": market.as_of.isoformat(),
            "latest_close": _display_decimal(market.latest_close),
            "previous_close": _display_decimal(market.previous_close),
            "daily_percentage_change": _display_decimal(market.daily_change_percent),
            "five_day_percentage_change": _display_decimal(market.five_day_change_percent),
            "trading_volume": market.volume,
        },
        "ranked_information_items": items,
        "market_context": _canonical_numeric_context(raw_market_context),
        "fundamental_and_valuation_context": (
            decision_fundamentals
            if asset_profile.asset_type is AssetType.EQUITY
            else {
                "applicability": "not_applicable",
                "reason": (
                    "Company revenue, earnings, margins, P/E, free cash flow, and "
                    "balance-sheet valuation do not apply to this commodity ETP."
                ),
            }
        ),
        "commodity_etp_context": serialized_commodity,
        "raw_numeric_evidence": {
            "market_data": raw_market_data,
            "market_context": raw_market_context,
            "fundamental_and_valuation_context": raw_fundamentals,
            "commodity_etp_context": (
                _json_safe(asdict(commodity_context)) if commodity_context else None
            ),
        },
        "event_selection": _selection_payload(selection, reference_by_item_id),
    }
    return payload, allowed, news_number, filing_number


COMMODITY_FACTOR_TERMS: dict[str, tuple[str, ...]] = {
    "supply": (
        "opec",
        "saudi",
        "russia",
        "production",
        "exports",
        "supply disruption",
    ),
    "demand": ("oil demand", "china demand", "us demand", "economic growth"),
    "inventories": ("inventory", "inventories", "eia crude", "stockpile"),
    "macro": ("us dollar", "dollar index", "interest rate", "recession", "growth risk"),
    "geopolitical": ("war", "sanction", "shipping", "red sea", "strait", "conflict"),
}


def _commodity_factor_coverage(items: list[dict[str, object]]) -> dict[str, object]:
    """Map recent, non-opinion evidence to oil-market factors without inventing facts."""

    coverage: dict[str, object] = {}
    decision_grade_depths = {
        EvidenceDepth.OFFICIAL_FILING_EXTRACT.value,
        EvidenceDepth.OFFICIAL_COMPANY_RELEASE.value,
        EvidenceDepth.FULL_PUBLIC_ARTICLE_EXTRACT.value,
        EvidenceDepth.SOURCE_PROVIDED_SUMMARY.value,
        EvidenceDepth.USER_PROVIDED_NOTES.value,
    }
    for category, terms in COMMODITY_FACTOR_TERMS.items():
        references: list[str] = []
        metadata_leads: list[str] = []
        for item in items:
            classification = str(item.get("classification") or "")
            if classification in {"opinion", "analyst commentary", "aggregation or unknown"}:
                continue
            text = " ".join(
                str(item.get(field) or "")
                for field in ("headline", "content_extract", "summary_supplied_by_source")
            ).lower()
            if any(term in text for term in terms):
                reference = str(item.get("reference"))
                if item.get("evidence_depth") in decision_grade_depths:
                    references.append(reference)
                else:
                    metadata_leads.append(reference)
        coverage[category] = {
            "status": (
                "verified recent evidence supplied"
                if references
                else "no verified recent factor evidence supplied"
            ),
            "references": references[:3],
            "metadata_leads_excluded_from_verified_factors": metadata_leads[:3],
            "rule": "Use only cited supplied evidence; absence is not evidence of no change.",
        }
    return coverage


VALUATION_METRICS = {"market capitalisation", "P/E", "price-to-sales"}


def _fundamental_evidence_context(
    fundamentals: FundamentalContext | None,
) -> dict[str, object] | None:
    """Expose only fully attributed metrics as decision-grade model evidence.

    Provider values remain in ``raw_numeric_evidence`` for audit and calculations, but
    incomplete aggregator snapshots cannot support investment conclusions.
    """

    if fundamentals is None:
        return None
    all_metrics = (*fundamentals.metrics, *fundamentals.peer_metrics)
    eligible = [metric for metric in all_metrics if _is_decision_grade_metric(metric)]
    selected: dict[str, object] = {}
    discrepancies: list[dict[str, object]] = []
    for metric in eligible:
        existing = selected.get(metric.name)
        serialized = _metric_evidence(metric)
        if not isinstance(existing, dict):
            selected[metric.name] = serialized
            continue
        if metric.official_source and not existing.get("official_source"):
            if existing.get("value") != serialized.get("value"):
                discrepancies.append(
                    _discrepancy(metric.name, official=serialized, third_party=existing)
                )
            selected[metric.name] = serialized
        elif not metric.official_source and existing.get("official_source"):
            if existing.get("value") != serialized.get("value"):
                discrepancies.append(
                    _discrepancy(metric.name, official=existing, third_party=serialized)
                )

    discrepancy_pairs = {
        (item.get("metric"), item.get("conflicting_source")) for item in discrepancies
    }
    for official_metric in (metric for metric in eligible if metric.official_source):
        official = selected.get(official_metric.name)
        if not isinstance(official, dict):
            continue
        for third_party_metric in all_metrics:
            pair = (official_metric.name, third_party_metric.source)
            if (
                third_party_metric.official_source
                or third_party_metric.name != official_metric.name
                or third_party_metric.value is None
                or third_party_metric.value == official_metric.value
                or pair in discrepancy_pairs
            ):
                continue
            discrepancies.append(
                _discrepancy(
                    official_metric.name,
                    official=official,
                    third_party={
                        "value": _scaled_display(third_party_metric.value),
                        "source": third_party_metric.source or fundamentals.source,
                    },
                )
            )
            discrepancy_pairs.add(pair)

    excluded = [
        {
            "name": metric.name,
            "source": metric.source or fundamentals.source,
            "reason": _metric_exclusion_reason(metric),
        }
        for metric in all_metrics
        if metric.value is not None and not _is_decision_grade_metric(metric)
    ]
    return {
        "source_preference": "official SEC filing or verified company IR document",
        "decision_grade_metrics": list(selected.values()),
        "excluded_non_decision_grade_metrics": excluded,
        "discrepancies": discrepancies,
        "overall_quality": (
            "official or fully attributed metrics available"
            if selected
            else "no decision-grade fundamental or valuation metrics available"
        ),
        "usage_rule": (
            "Excluded metrics must not support valuation conclusions or a weakened thesis."
        ),
    }


def _is_decision_grade_metric(metric: object) -> bool:
    required = (
        getattr(metric, "source", None),
        getattr(metric, "reporting_period", None),
        getattr(metric, "currency", None),
        getattr(metric, "unit", None),
        getattr(metric, "as_of", None),
        getattr(metric, "calculation_method", None),
    )
    if not getattr(metric, "decision_grade", False) or not all(required):
        return False
    if getattr(metric, "name", None) in VALUATION_METRICS:
        valuation_fields = (
            getattr(metric, "market_capitalisation_used", None),
            getattr(metric, "revenue_period_used", None),
            getattr(metric, "shares_outstanding_used", None),
            getattr(metric, "price_date", None),
            getattr(metric, "resulting_ratio", None),
        )
        return all(value is not None for value in valuation_fields)
    return True


def _metric_evidence(metric: object) -> dict[str, object]:
    result = _json_safe(asdict(metric))
    if not isinstance(result, dict):
        raise TypeError("Fundamental metric must serialize to an object.")
    canonical = _canonical_numeric_context(result)
    if not isinstance(canonical, dict):
        raise TypeError("Canonical fundamental metric must be an object.")
    if getattr(metric, "name", None) in VALUATION_METRICS:
        canonical["valuation_calculation"] = {
            "market_capitalisation": _scaled_display(
                getattr(metric, "market_capitalisation_used")
            ),
            "revenue_period_used": getattr(metric, "revenue_period_used"),
            "shares_outstanding_used": _scaled_display(
                getattr(metric, "shares_outstanding_used")
            ),
            "price_date": getattr(metric, "price_date").isoformat(),
            "calculation_method": getattr(metric, "calculation_method"),
            "resulting_ratio": _display_decimal(getattr(metric, "resulting_ratio")),
        }
    return canonical


def _metric_exclusion_reason(metric: object) -> str:
    missing = [
        label
        for label, value in (
            ("source", getattr(metric, "source", None)),
            ("reporting period", getattr(metric, "reporting_period", None)),
            ("currency", getattr(metric, "currency", None)),
            ("units", getattr(metric, "unit", None)),
            ("as-of date", getattr(metric, "as_of", None)),
            ("calculation method", getattr(metric, "calculation_method", None)),
        )
        if not value
    ]
    if getattr(metric, "name", None) in VALUATION_METRICS:
        missing.extend(
            label
            for label, value in (
                ("market capitalisation used", getattr(metric, "market_capitalisation_used", None)),
                ("revenue period used", getattr(metric, "revenue_period_used", None)),
                ("shares outstanding used", getattr(metric, "shares_outstanding_used", None)),
                ("price date", getattr(metric, "price_date", None)),
                ("resulting ratio", getattr(metric, "resulting_ratio", None)),
            )
            if value is None
        )
    detail = ", ".join(dict.fromkeys(missing)) or "decision-grade verification"
    return f"not decision-grade; missing {detail}"


def _discrepancy(
    name: str, *, official: dict[str, object], third_party: dict[str, object]
) -> dict[str, object]:
    return {
        "metric": name,
        "preferred_value": official.get("value"),
        "preferred_source": official.get("source"),
        "conflicting_third_party_value": third_party.get("value"),
        "conflicting_source": third_party.get("source"),
        "resolution": "official value used; discrepancy flagged",
    }


def render_markdown_report(
    prepared: PreparedResearch,
    generated: GeneratedResearchReport,
) -> str:
    report = generated.report
    lines = [
        f"# Evidence-grounded research report: {prepared.ticker}",
        "",
        f"- Report date: {prepared.report_date.isoformat()}",
        f"- Model: {generated.model}",
        f"- Evidence supplied: {prepared.news_count} news item(s), "
        f"{prepared.filing_count} filing item(s), and market data",
        f"- Token usage: {_usage_text(generated)}",
        "- This is research support, not personalised financial advice or a buy/sell instruction.",
        "",
    ]
    _add_statement_section(lines, "A. Executive summary", report.executive_summary)
    _add_statement_section(lines, "B. Verified facts", report.verified_facts)
    _add_statement_section(lines, "C. Market movement", report.market_movement)
    _add_important_section(lines, report.important_information)
    _add_statement_section(
        lines, "E. Possible short-term implications", report.possible_short_term_implications
    )
    _add_statement_section(
        lines,
        "F. Possible medium- and long-term implications",
        report.possible_medium_and_long_term_implications,
    )
    _add_statement_section(lines, "G. Bull case", report.bull_case)
    _add_statement_section(lines, "H. Bear case", report.bear_case)
    _add_statement_section(lines, "I. Key risks", report.key_risks)
    _add_statement_section(
        lines, "J. Upcoming factors to monitor", report.upcoming_factors_to_monitor
    )
    lines.extend(["## K. Evidence gaps", ""])
    lines.extend(f"- {gap}" for gap in report.evidence_gaps)
    lines.extend(
        [
            "",
            "## L. Investment thesis status",
            "",
            report.investment_thesis_status.value,
            "",
            "## M. Suggested investor response",
            "",
            *[f"- {response.value}" for response in report.suggested_investor_responses],
            "",
            "## N. Confidence level",
            "",
            f"**{report.confidence_level.value}** — {report.confidence_rationale}",
            "",
            "Confidence describes the supplied evidence quality, not future-price predictability.",
            "",
        ]
    )
    return "\n".join(lines)


def _add_statement_section(
    lines: list[str], title: str, statements: list[EvidenceStatement]
) -> None:
    lines.extend([f"## {title}", ""])
    for statement in statements:
        references = " ".join(statement.references)
        lines.append(f"- {statement.statement} {references}")
        lines.append(
            f"  - Evidence: {statement.evidence_basis.value}. Uncertainty: {statement.uncertainty}"
        )
    lines.append("")


def _add_important_section(lines: list[str], items: list[ImportantInformation]) -> None:
    lines.extend(["## D. Important information", ""])
    for item in items:
        lines.append(f"- **{item.reference} — {item.source}** ({item.classification})")
        lines.append(f"  - Why it may matter: {item.why_it_may_matter}")
        lines.append(f"  - Evidence: {item.evidence_basis.value}. Uncertainty: {item.uncertainty}")
    lines.append("")


def _usage_text(generated: GeneratedResearchReport) -> str:
    usage = generated.usage
    if all(
        value is None for value in (usage.input_tokens, usage.output_tokens, usage.total_tokens)
    ):
        return "not provided by API"
    return (
        f"input={usage.input_tokens if usage.input_tokens is not None else 'unknown'}, "
        f"output={usage.output_tokens if usage.output_tokens is not None else 'unknown'}, "
        f"total={usage.total_tokens if usage.total_tokens is not None else 'unknown'}"
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _depth_counts(items: list[ExtractedEvidence]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.evidence_depth.value] = counts.get(item.evidence_depth.value, 0) + 1
    return counts


def _selection_payload(
    selection: EvidenceSelection | None,
    reference_by_item_id: dict[int, str],
) -> dict[str, object] | None:
    if selection is None:
        return None
    return {
        "main_event_clusters": [
            {
                "cluster_id": cluster.cluster_id,
                "label": cluster.label,
                "related_item_count": len(cluster.items),
                "selection_score": cluster.selection_score,
                "selected_reference": reference_by_item_id.get(id(cluster.selected)),
            }
            for cluster in selection.main_clusters
        ],
        "secondary_item_count": len(selection.secondary_items),
        "noise_item_count": len(selection.noise_items),
        "noise_references": [
            reference_by_item_id[id(item)]
            for item in selection.noise_items
            if id(item) in reference_by_item_id
        ],
    }


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime, Decimal)):
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    return value


def _display_decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def _canonical_numeric_context(value: object, key: str = "") -> object:
    if isinstance(value, list):
        return [_canonical_numeric_context(item, key) for item in value]
    if not isinstance(value, dict):
        decimal = _decimal_or_none(value)
        if decimal is None:
            return value
        lowered = key.lower()
        if "volume" in lowered:
            return int(decimal)
        if "percent" in lowered or "percentage" in lowered:
            return _display_decimal(decimal)
        if "price" in lowered:
            return _display_decimal(decimal)
        return value
    if "value" in value and "unit" in value:
        result = {
            str(child_key): _canonical_numeric_context(child, str(child_key))
            for child_key, child in value.items()
        }
        decimal = _decimal_or_none(value.get("value"))
        unit = str(value.get("unit", "")).lower()
        if decimal is None:
            return result
        if unit == "ratio":
            result["value"] = f"{_display_decimal(decimal * Decimal('100'))}%"
            result["unit"] = "percent"
        elif unit == "currency":
            result["value"] = _scaled_display(decimal)
        elif unit == "shares":
            result["value"] = f"{decimal.quantize(Decimal('1')):,}"
        elif unit == "multiple":
            result["value"] = _display_decimal(decimal)
        return result
    return {
        str(child_key): _canonical_numeric_context(child, str(child_key))
        for child_key, child in value.items()
    }


def _scaled_display(value: Decimal) -> str:
    absolute = abs(value)
    for label, scale in (
        ("trillion", Decimal("1000000000000")),
        ("billion", Decimal("1000000000")),
        ("million", Decimal("1000000")),
    ):
        if absolute >= scale:
            return f"{_display_decimal(value / scale)} {label}"
    return _display_decimal(value)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (ArithmeticError, ValueError):
        return None


def _normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if not TICKER_PATTERN.fullmatch(normalized):
        raise ResearchDataUnavailableError(
            "Ticker must contain only letters, numbers, '.', '-', '^' or '='."
        )
    return normalized
