"""News read endpoints for the monitoring UI.

Source material and AI analysis are returned as separate blocks so the UI can never
present an AI interpretation as a source fact. These handlers are read-only.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ai_trader.application.container import Container

router = APIRouter()


def container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def _source_block(item: Any) -> dict[str, object]:
    """Exactly what the publisher provided. No AI fields."""
    return {
        "item_id": item.item_id,
        "source_name": item.source_name,
        "source_type": item.source_type.value,
        "trust_level": item.trust_level.value,
        "is_official": item.source_type.value in ("OFFICIAL", "COMPANY"),
        "headline": item.headline,
        "canonical_url": item.canonical_url,
        "url": item.url,
        "published_at": item.published_at.isoformat(),
        "received_at": item.received_at.isoformat(),
        "category": item.category.value,
        "symbols": list(item.symbols),
        "excerpt": item.excerpt,
        "content_hash": item.content_hash,
        "cluster_id": item.cluster_id,
    }


def _analysis_block(analysis: Any) -> dict[str, object] | None:
    if analysis is None:
        return None
    return {
        "analysis_id": analysis.analysis_id,
        "status": analysis.status.value,
        "event_type": analysis.event_type,
        "summary": analysis.summary,
        "affected_symbols": list(analysis.affected_symbols),
        "materiality": analysis.materiality.value if analysis.materiality else None,
        "direction": analysis.direction.value if analysis.direction else None,
        "horizon": analysis.horizon.value if analysis.horizon else None,
        "confidence": analysis.confidence,
        "requires_portfolio_review": analysis.requires_portfolio_review,
        "evidence": list(analysis.evidence),
        "uncertainties": list(analysis.uncertainties),
        "analysis_started_at": analysis.analysis_started_at.isoformat()
        if analysis.analysis_started_at
        else None,
        "analysis_completed_at": analysis.analysis_completed_at.isoformat()
        if analysis.analysis_completed_at
        else None,
        "portfolio_review_at": analysis.portfolio_review_at.isoformat()
        if analysis.portfolio_review_at
        else None,
        "failure_reason": analysis.failure_reason,
        "model": (analysis.provider_metadata or {}).get("model"),
        "latency_ms": (analysis.provider_metadata or {}).get("latency_ms"),
    }


def _row(app: Container, item: Any) -> dict[str, object]:
    repository = app.news_store()
    analysis = repository.latest_analysis_for_item(item.item_id)
    members = repository.cluster_members(item.cluster_id) if item.cluster_id else []
    source = _source_block(item)
    source["related_source_count"] = max(0, len(members) - 1)
    analysis_block = _analysis_block(analysis)
    return {
        "source": source,
        "ai_analysis": analysis_block,
        "review_triggered": bool(analysis and analysis.portfolio_review_at is not None),
    }


@router.get("/news")
async def news_feed(request: Request, limit: int = 100) -> list[dict[str, object]]:
    app = container(request)
    app.ensure_news_schema()
    repository = app.news_store()
    return [_row(app, item) for item in repository.recent_items(limit=limit)]


@router.get("/news/recent")
async def news_recent(request: Request, limit: int = 25) -> list[dict[str, object]]:
    return await news_feed(request, limit=limit)


@router.get("/news/latency")
async def news_latency(request: Request, limit: int = 25) -> dict[str, object]:
    """Measured news-to-trade latencies, newest first."""
    app = container(request)
    app.ensure_news_schema()
    rows = app.news_store().latency_rows(limit=limit)
    measured: list[int] = [
        value for row in rows if isinstance((value := row.get("news_to_trade_ms")), int)
    ]
    return {
        "rows": rows,
        "count": len(rows),
        "news_to_trade_ms": _summary(measured),
    }


@router.get("/news/providers")
async def news_providers(request: Request) -> dict[str, object]:
    """Live/degraded/offline status for every configured source.

    Ingestion only runs inside a shadow session, so this reports the configured
    intervals and queue state whether or not a session is active.
    """
    app = container(request)
    app.ensure_news_schema()
    intelligence = app.news_intelligence()
    configured: list[dict[str, object]] = [
        {
            "source_id": source.source_id,
            "name": source.name,
            "source_type": source.source_type.value,
            "trust_level": source.trust_level.value,
            "enabled": source.enabled,
            "poll_interval_seconds": app.settings.poll_interval_for(source.source_id),
            "status": "STOPPED",
            "last_item_at": None,
            "seconds_since_last_item": None,
            "last_error": None,
            "consecutive_failures": 0,
        }
        for source in sorted(intelligence.repository.list_sources(), key=lambda s: s.source_id)
        if source.enabled
    ]
    live = app.realtime_news
    by_id = {status["source_id"]: status for status in (live.provider_status() if live else [])}
    for entry in configured:
        status = by_id.get(entry["source_id"])
        if status is None:
            continue
        for key in (
            "status",
            "last_item_at",
            "seconds_since_last_item",
            "last_error",
            "consecutive_failures",
        ):
            entry[key] = status[key]
    # A source that is live but not in the registry must still be reported.
    configured.extend(
        status
        for status in by_id.values()
        if status["source_id"] not in {entry["source_id"] for entry in configured}
    )
    return {
        "ingestion_running": live is not None and live.running,
        "providers": configured,
        "queue": {
            "depth": intelligence.pipeline().queue.qsize(),
            "capacity": intelligence.pipeline().queue.maxsize,
            "max_concurrent_analyses": app.settings.news_ai_max_concurrent_requests,
        },
        "pipeline": intelligence.pipeline().stats.as_dict(),
    }


def _summary(values: list[int]) -> dict[str, object]:
    """Min/median/max for a latency series. Empty input reports n/a rather than zero."""
    if not values:
        return {"samples": 0, "min_ms": None, "median_ms": None, "max_ms": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) // 2
    return {
        "samples": len(ordered),
        "min_ms": ordered[0],
        "median_ms": median,
        "max_ms": ordered[-1],
    }


@router.get("/news/sources")
async def news_sources(request: Request) -> list[dict[str, object]]:
    app = container(request)
    # Going through the service guarantees the registry is upserted, so the UI sees
    # the configured sources even on a database that has never been polled.
    intelligence = app.news_intelligence()
    return [
        {
            "source_id": source.source_id,
            "name": source.name,
            "homepage": source.homepage,
            "source_type": source.source_type.value,
            "trust_level": source.trust_level.value,
            "default_category": source.default_category.value,
            "enabled": source.enabled,
            "notes": source.notes,
        }
        for source in intelligence.repository.list_sources()
    ]


@router.get("/news/symbol/{symbol}")
async def news_for_symbol(request: Request, symbol: str) -> list[dict[str, object]]:
    app = container(request)
    app.ensure_news_schema()
    repository = app.news_store()
    return [_row(app, item) for item in repository.items_for_symbol(symbol.upper())]


# The detail route is declared last so it cannot shadow the literal sub-paths above.
@router.get("/news/{item_id}")
async def news_detail(request: Request, item_id: str) -> dict[str, object]:
    app = container(request)
    app.ensure_news_schema()
    repository = app.news_store()
    item = repository.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="news item not found")
    return _row(app, item)
