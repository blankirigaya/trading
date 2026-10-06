"""AI news analyst.

The analyst reads source material and returns a strictly validated
`NewsAnalysis`. It never emits a trade action, and there is deliberately no mapping
from event type to direction: significance is the model's judgement, not a rule.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ai_trader.domain.decisions import ProviderMetadata
from ai_trader.domain.news import (
    Direction,
    Horizon,
    Materiality,
    NewsAnalysis,
    NewsItem,
    NewsPortfolioReviewContext,
)

PROMPT_NAME = "news_analyst"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are a financial news analyst for an autonomous paper-trading system.

You read a single permitted news item or official filing and assess it.

Rules:
- Judge significance from the supplied facts only. Do not assume sentiment from the event name.
- A large order win is not automatically positive; a resignation is not automatically negative.
- Consider the source and its trust level when weighing certainty.
- If the material is ambiguous, use UNCLEAR rather than guessing.
- Set requires_portfolio_review only when a human-scale portfolio decision may be warranted.
- You do not decide whether to buy, sell, or hold. You do not size positions.
- Return only the requested JSON structure.

Fields:
  event_type        short label for what happened
  summary           two or three factual sentences
  affected_symbols  tickers or company identifiers involved
  materiality       LOW | MEDIUM | HIGH | VERY_HIGH | UNCLEAR
  direction         POSITIVE | NEGATIVE | MIXED | NEUTRAL | UNCLEAR
  horizon           MINUTES | INTRADAY | DAYS | WEEKS | LONG_TERM | UNCLEAR
  confidence        0..1 confidence in your own assessment
  requires_portfolio_review  boolean
  evidence          short quoted or paraphrased facts you relied on
  uncertainties     what you could not determine
"""

# Hard ceiling on how much source text is sent, to bound latency and cost.
MAX_INPUT_CHARS = 6_000


def build_user_prompt(
    item: NewsItem,
    *,
    related_headlines: tuple[str, ...] = (),
    market_context: str | None = None,
    document_text: str | None = None,
) -> str:
    parts = [
        f"source: {item.source_name}",
        f"source_type: {item.source_type.value}",
        f"trust_level: {item.trust_level.value}",
        f"category: {item.category.value}",
        f"published_at: {item.published_at.isoformat()}",
        f"headline: {item.headline}",
    ]
    if item.company:
        parts.append(f"company: {item.company}")
    if item.symbols:
        parts.append(f"mentioned_symbols: {', '.join(item.symbols)}")
    if related_headlines:
        parts.append("related reports of the same story:")
        parts.extend(f"  - {headline}" for headline in related_headlines[:6])
    if market_context:
        parts.append(f"market context: {market_context[:800]}")
    if document_text:
        parts.append("filing text (excerpt):")
        parts.append(document_text[:MAX_INPUT_CHARS])
    elif item.excerpt:
        parts.append("permitted article excerpt:")
        parts.append(item.excerpt[:MAX_INPUT_CHARS])
    return "\n".join(parts)


class NewsAnalysisResponse(BaseModel):
    """The exact structured shape the model must return."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: str = Field(min_length=2, max_length=80)
    summary: str = Field(min_length=10, max_length=1200)
    affected_symbols: list[str] = Field(default_factory=list, max_length=25)
    materiality: Materiality
    direction: Direction
    horizon: Horizon
    confidence: float = Field(ge=0, le=1)
    requires_portfolio_review: bool
    evidence: list[str] = Field(default_factory=list, max_length=12)
    uncertainties: list[str] = Field(default_factory=list, max_length=12)


def to_analysis(
    response: NewsAnalysisResponse,
    *,
    item: NewsItem,
    cluster_id: str,
    started_at: datetime,
    completed_at: datetime,
    metadata: ProviderMetadata,
) -> NewsAnalysis:
    return NewsAnalysis(
        analysis_id=str(uuid4()),
        cluster_id=cluster_id,
        item_id=item.item_id,
        status="COMPLETED",  # type: ignore[arg-type]
        event_type=response.event_type,
        summary=response.summary,
        affected_symbols=tuple(dict.fromkeys(response.affected_symbols)),
        materiality=response.materiality,
        direction=response.direction,
        horizon=response.horizon,
        confidence=response.confidence,
        requires_portfolio_review=response.requires_portfolio_review,
        evidence=tuple(response.evidence),
        uncertainties=tuple(response.uncertainties),
        analysis_started_at=started_at,
        analysis_completed_at=completed_at,
        provider_metadata=metadata.model_dump(mode="json"),
    )


def failed_analysis(
    item: NewsItem,
    cluster_id: str,
    reason: str,
    started_at: datetime | None = None,
) -> NewsAnalysis:
    """A stored, auditable failure. No trade is implied."""
    return NewsAnalysis(
        analysis_id=str(uuid4()),
        cluster_id=cluster_id,
        item_id=item.item_id,
        status="FAILED",  # type: ignore[arg-type]
        requires_portfolio_review=False,
        analysis_started_at=started_at,
        analysis_completed_at=datetime.now(UTC),
        failure_reason=reason,
    )


def pending_analysis(item: NewsItem, cluster_id: str) -> NewsAnalysis:
    return NewsAnalysis(
        analysis_id=str(uuid4()),
        cluster_id=cluster_id,
        item_id=item.item_id,
        status="PENDING",  # type: ignore[arg-type]
        requires_portfolio_review=False,
    )


def to_review_context(
    analysis: NewsAnalysis, item: NewsItem, related_count: int = 1
) -> NewsPortfolioReviewContext:
    """Preserve provenance for the Portfolio Manager and Jev.

    News-analysis confidence is carried separately from any trading confidence;
    this context contains no action instruction.
    """
    return NewsPortfolioReviewContext(
        cluster_id=analysis.cluster_id,
        item_id=item.item_id,
        analysis_id=analysis.analysis_id,
        source_name=item.source_name,
        source_type=item.source_type,
        trust_level=item.trust_level,
        published_at=item.published_at,
        headline=item.headline,
        excerpt=item.excerpt,
        ai_summary=analysis.summary,
        ai_event_type=analysis.event_type,
        ai_materiality=analysis.materiality,
        ai_direction=analysis.direction,
        ai_horizon=analysis.horizon,
        ai_confidence=analysis.confidence,
        ai_evidence=analysis.evidence,
        ai_uncertainties=analysis.uncertainties,
        requires_portfolio_review=analysis.requires_portfolio_review,
        related_item_count=related_count,
        symbols=tuple(analysis.affected_symbols) or item.symbols,
    )


def load_system_prompt() -> str:
    prompt_file = Path(__file__).resolve().parents[1] / "ai" / "prompts" / "news_analyst_v1.txt"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")
    return SYSTEM_PROMPT
