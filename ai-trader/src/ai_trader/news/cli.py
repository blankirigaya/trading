"""Developer commands for the news intelligence layer."""

import asyncio

from ai_trader.ai.remote_model import OpenAICompatibleAIProvider
from ai_trader.ai.retries import RetryingAIProvider
from ai_trader.config import Settings
from ai_trader.news.pipeline import poll_sources
from ai_trader.news.service import NewsIntelligence
from ai_trader.news.sources import all_sources, build_provider
from ai_trader.persistence.database import create_database_engine
from ai_trader.persistence.models import Base
from ai_trader.persistence.news_repository import NewsRepository


def _ensure_schema(database_url: str) -> None:
    """Create news tables for SQLite so a local run works without alembic."""
    if database_url.startswith("sqlite"):
        engine = create_database_engine(database_url)
        Base.metadata.create_all(engine)
        engine.dispose()


def _build_intelligence(settings: Settings) -> NewsIntelligence:
    _ensure_schema(settings.database_url)
    engine = create_database_engine(settings.database_url)
    repository = NewsRepository(engine)
    provider = None
    if settings.ai_base_url and settings.ai_model:
        provider = RetryingAIProvider(
            OpenAICompatibleAIProvider(
                settings.ai_base_url,
                settings.ai_model,
                settings.ai_api_key,
                settings.ai_timeout_seconds,
                settings.ai_max_concurrent_requests,
            ),
            settings.news_ai_max_retries,
        )
    return NewsIntelligence(settings, repository, provider)


def news_test(settings: Settings) -> int:
    """Report each provider's reachability and item count. Never fakes success."""
    print("NEWS PROVIDER CONNECTIVITY")
    print("=" * 62)
    print(f"{'source':<24} {'type':<12} {'trust':<10} {'status':<8} items")
    print("-" * 62)
    results = asyncio.run(poll_sources(all_sources(), timeout=20))
    failures = 0
    for result in results:
        source = next((s for s in all_sources() if s.source_id == result.source_id), None)
        if source is None:
            continue
        status = "OK" if result.ok else "FAILED"
        if not result.ok:
            failures += 1
        print(
            f"{source.source_id:<24} {source.source_type.value:<12} "
            f"{source.trust_level.value:<10} {status:<8} {result.item_count}"
        )
        if result.error:
            print(f"{'':<24} reason: {result.error}")

    print("-" * 62)
    parsed_total = sum(result.item_count for result in results)
    print(f"providers: {len(results)}  parsed items: {parsed_total}  failures: {failures}")
    if parsed_total == 0:
        print(
            "NOTE: no items were parsed. Official regulator/exchange listings are "
            "JavaScript-rendered or block automated clients, and their private JSON "
            "APIs are undocumented so they are intentionally not used."
        )
    return 0 if parsed_total else 1


def news_poll_once(settings: Settings) -> int:
    """Poll every enabled source once and report what was actually retrieved."""
    intelligence = _build_intelligence(settings)
    summary = asyncio.run(intelligence.poll(timeout=25))
    print("NEWS POLL")
    print("=" * 62)
    for result in summary.provider_results:
        status = "OK" if result.ok else "FAILED"
        print(f"{result.source_id:<24} {status:<8} items={result.item_count}")
        if result.error:
            print(f"{'':<24} reason: {result.error}")
    print("-" * 62)
    print(f"received:  {summary.received}")
    print(f"stored:    {summary.stored}")
    print(f"duplicates:{summary.duplicates}")
    print(f"clusters:  {summary.clusters}")
    print(f"enqueued for AI analysis: {summary.enqueued}")
    print(f"dropped (queue full):   {intelligence.pipeline().stats.dropped_queue_full}")
    if summary.received == 0:
        print("No news was retrieved. This is reported as-is; nothing was fabricated.")
    return 0


def news_analyse_latest(settings: Settings, limit: int = 5) -> int:
    """Analyse the most recent stored items with the remote AI."""
    intelligence = _build_intelligence(settings)
    if not intelligence.items:
        print("No stored news items. Run 'news poll --once' first.")
        return 1
    if intelligence.ai_provider is None:
        print("AI not configured: set AI_BASE_URL and AI_MODEL. No analysis was fabricated.")
        return 1

    analyses = asyncio.run(intelligence.analyse_latest(limit=limit))
    if not analyses:
        print("No unanalysed news items found. Nothing was fabricated.")
        return 0
    print("NEWS AI ANALYSIS")
    print("=" * 62)
    for analysis in analyses:
        item = intelligence.items.get(analysis.item_id)
        print(f"\n{item.headline if item else analysis.item_id}")
        print(
            f"  source      : {item.source_name if item else 'n/a'}"
            f" ({item.source_type.value if item else 'n/a'}/{item.trust_level.value if item else 'n/a'})"
        )
        print(f"  status      : {analysis.status.value}")
        if analysis.status.value != "COMPLETED":
            print(f"  failure     : {analysis.failure_reason}")
            continue
        print(f"  event_type  : {analysis.event_type}")
        print(f"  materiality : {analysis.materiality.value if analysis.materiality else 'n/a'}")
        print(f"  direction   : {analysis.direction.value if analysis.direction else 'n/a'}")
        print(f"  horizon     : {analysis.horizon.value if analysis.horizon else 'n/a'}")
        print(f"  confidence  : {analysis.confidence}")
        print(f"  symbols     : {', '.join(analysis.affected_symbols) or 'n/a'}")
        print(f"  review      : {analysis.requires_portfolio_review}")
        if analysis.evidence:
            print("  evidence    :")
            for line in analysis.evidence[:5]:
                print(f"    - {line}")
        if analysis.uncertainties:
            print("  uncertainties:")
            for line in analysis.uncertainties[:5]:
                print(f"    - {line}")
    return 0


__all__ = [
    "build_provider",
    "news_analyse_latest",
    "news_poll_once",
    "news_test",
]
