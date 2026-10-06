"""News context for a portfolio review.

This module builds the exact text the Portfolio Manager and Jev receive. It keeps two
things rigorously apart:

``ORIGINAL SOURCE INFORMATION``
    What the publisher said: headline, source, classification, timestamps, permitted
    excerpt.

``AI NEWS INTERPRETATION``
    What the news analyst concluded: materiality, direction, horizon, confidence,
    evidence, uncertainties. Labelled as a model judgement, with its own confidence
    that must never be read as trading confidence.

There is deliberately no action field and no mapping from event type to a trade. The
bundle is context; the Portfolio Manager decides, Jev challenges, and only the resulting
structured decision can reach paper execution.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai_trader.domain.news import (
    NewsAnalysis,
    NewsItem,
    NewsPortfolioReviewContext,
)

SOURCE_HEADER = "=== ORIGINAL SOURCE INFORMATION (publisher statements, unverified) ==="
INTERPRETATION_HEADER = "=== AI NEWS INTERPRETATION (model judgement, not a trading signal) ==="

MAX_ITEMS_IN_BUNDLE = 12


@dataclass(frozen=True)
class NewsReviewBundle:
    """Everything the AI should know about recent news, with provenance intact."""

    source_items: tuple[NewsItem, ...] = ()
    analyses: tuple[NewsAnalysis, ...] = ()
    trigger_item_ids: tuple[str, ...] = ()
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_empty(self) -> bool:
        return not self.source_items and not self.analyses

    def concerns(self, symbols: set[str]) -> bool:
        """True when any analysed item names one of the given symbols."""
        return bool(self.affected_symbols & {s.upper() for s in symbols})

    @property
    def affected_symbols(self) -> set[str]:
        found: set[str] = set()
        for item in self.source_items:
            found.update(symbol.upper() for symbol in item.symbols)
        for analysis in self.analyses:
            found.update(symbol.upper() for symbol in analysis.affected_symbols)
        return found

    def as_prompt(self, *, held_symbols: set[str] | None = None) -> str:
        """Render the bundle for the portfolio prompt.

        Source facts come first and are labelled as such; model interpretations follow
        under their own header. The order matters so the model weighs what was reported
        separately from what was inferred.
        """
        if self.is_empty:
            return ""
        held = {s.upper() for s in (held_symbols or set())}
        lines: list[str] = ["RECENT NEWS CONTEXT", ""]

        if self.source_items:
            lines.append(SOURCE_HEADER)
            for item in self._newest_items(self.source_items):
                classification = (
                    "OFFICIAL" if item.source_type.value in ("OFFICIAL", "COMPANY") else "SECONDARY"
                )
                marker = (
                    " [CONCERNS A HELD POSITION]"
                    if held & {s.upper() for s in item.symbols}
                    else ""
                )
                lines.append(
                    f"- [{classification} / {item.trust_level.value}] {item.source_name}"
                    f"{marker}\n"
                    f"  headline: {item.headline}\n"
                    f"  published: {item.published_at.isoformat()} | received: {item.received_at.isoformat()}"
                )
                if item.version > 1:
                    lines.append(f"  revision: version {item.version} supersedes an earlier item")
                if item.symbols:
                    lines.append(f"  symbols named by source: {', '.join(item.symbols)}")
                if item.excerpt:
                    lines.append(f"  excerpt: {item.excerpt[:600]}")
            lines.append("")

        if self.analyses:
            lines.append(INTERPRETATION_HEADER)
            for analysis in self._newest_analyses(self.analyses):
                lines.append(
                    f"- {analysis.event_type or 'UNCLASSIFIED'} | "
                    f"materiality={analysis.materiality.value if analysis.materiality else 'UNCLEAR'} | "
                    f"direction={analysis.direction.value if analysis.direction else 'UNCLEAR'} | "
                    f"horizon={analysis.horizon.value if analysis.horizon else 'UNCLEAR'} | "
                    f"analyst_confidence={analysis.confidence if analysis.confidence is not None else 'n/a'}"
                )
                if analysis.summary:
                    lines.append(f"  interpretation: {analysis.summary}")
                if analysis.affected_symbols:
                    lines.append(f"  symbols: {', '.join(analysis.affected_symbols)}")
                for fact in analysis.evidence[:6]:
                    lines.append(f"  cited evidence: {fact}")
                for gap in analysis.uncertainties[:4]:
                    lines.append(f"  analyst uncertainty: {gap}")
                if analysis.requires_portfolio_review:
                    lines.append("  analyst asked for an immediate portfolio review")
            lines.append("")

        lines.append(
            "The blocks above are separate. Analyst confidence is confidence in an "
            "interpretation, not a trading confidence. Weigh them against live prices "
            "and the current book, and decide independently: news may justify opening, "
            "adding to, reducing, or closing a position, or doing nothing at all."
        )
        return "\n".join(lines)

    @staticmethod
    def _newest_items(items: tuple[NewsItem, ...]) -> list[NewsItem]:
        """Freshest evidence first, so the model weighs the latest information."""
        return sorted(items, key=lambda item: item.received_at, reverse=True)[:MAX_ITEMS_IN_BUNDLE]

    @staticmethod
    def _newest_analyses(analyses: tuple[NewsAnalysis, ...]) -> list[NewsAnalysis]:
        return sorted(
            analyses,
            key=lambda entry: entry.analysis_completed_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )[:MAX_ITEMS_IN_BUNDLE]


def build_bundle(
    *,
    trigger: tuple[tuple[NewsItem, NewsAnalysis], ...] = (),
    recent_items: tuple[NewsItem, ...] = (),
    recent_analyses: tuple[NewsAnalysis, ...] = (),
) -> NewsReviewBundle:
    """Assemble a bundle, de-duplicating by item id and keeping triggers first.

    ``trigger`` is the news that caused this review. ``recent_*`` is the background
    window: everything since the previous review, including news about candidate stocks.
    """
    seen: set[str] = set()
    items: list[NewsItem] = []
    analyses: list[NewsAnalysis] = []
    trigger_ids: list[str] = []

    for item, analysis in trigger:
        trigger_ids.append(item.item_id)
        if item.item_id not in seen:
            seen.add(item.item_id)
            items.append(item)
        if analysis.analysis_id not in {a.analysis_id for a in analyses}:
            analyses.append(analysis)

    for item in recent_items:
        if item.item_id not in seen:
            seen.add(item.item_id)
            items.append(item)
    known = {a.analysis_id for a in analyses}
    for analysis in recent_analyses:
        if analysis.analysis_id not in known:
            analyses.append(analysis)

    return NewsReviewBundle(
        source_items=tuple(items),
        analyses=tuple(analyses),
        trigger_item_ids=tuple(dict.fromkeys(trigger_ids)),
    )


def context_from(item: NewsItem, analysis: NewsAnalysis) -> NewsPortfolioReviewContext:
    """Convenience wrapper so callers do not need the analyst module."""
    from ai_trader.news.analyst import to_review_context

    return to_review_context(analysis, item)
