"""Read-only and control surfaces for the monitoring UI.

Handlers stay thin: they read container state or delegate to the shadow runtime.
No orchestration, market logic, or trading judgment lives in this module.
"""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ai_trader.api.events import new_event
from ai_trader.application.container import Container

router = APIRouter()


def container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/system/status")
async def system_status(request: Request) -> dict[str, object]:
    app = container(request)
    return {
        "mode": app.settings.app_mode,
        "environment": app.settings.app_env,
        "trading_enabled": app.settings.trading_enabled,
        "market_provider": app.settings.market_provider,
        "market_connected": app.market_connected,
        "execution_provider": "PaperBroker",
        "execution_broker": "paper",
        "live_order_execution": False,
        "fyers_market_data_only": True,
        "ai_provider": app.settings.ai_provider,
        "ai_server": app.configured_ai_server,
        "ai_model": app.settings.ai_model,
        "ai_inference_location": "remote",
        "jev_enabled": app.settings.jev_enabled,
        "jev_model": app.settings.jev_model if app.settings.jev_enabled else None,
        "kill_switch_state": app.kill_switch.enabled,
        "session_running": app.runtime is not None and app.runtime.running,
        "shadow_symbols": list(app.settings.shadow_symbols),
        "shadow_initial_capital_inr": str(app.settings.shadow_initial_capital_inr),
        "portfolio_review_interval_seconds": app.settings.portfolio_review_interval_seconds,
        "database": "connected",
        "news_enabled": app.settings.news_enabled,
        # Cached provider health. This endpoint never performs an inference request.
        **app.ai_health.snapshot(),
    }


@router.get("/market/status")
async def market_status(request: Request) -> dict[str, object]:
    app = container(request)
    return {
        "provider": app.settings.market_provider,
        "connected": app.market_connected,
        "symbols": list(app.settings.shadow_symbols),
        "feed_mode": app.settings.fyers_feed_mode,
        "fyers_market_data_only": True,
    }


@router.get("/market/snapshots")
async def market_snapshots(request: Request) -> list[dict[str, object]]:
    """Latest normalized market state per instrument, as produced by the feed."""
    return container(request).market_snapshots()


@router.get("/market/candles")
async def market_candles(request: Request, symbol: str) -> list[dict[str, object]]:
    return container(request).market_candles(symbol)


@router.get("/portfolio")
async def portfolio(request: Request) -> dict[str, object]:
    return (await container(request).broker.get_account()).model_dump(mode="json")


@router.get("/shadow/portfolio")
async def shadow_portfolio(request: Request) -> dict[str, object]:
    return (await container(request).broker.get_account()).model_dump(mode="json")


@router.get("/positions")
async def positions(request: Request) -> list[dict[str, object]]:
    return [
        position.model_dump(mode="json")
        for position in (await container(request).broker.get_positions()).positions
    ]


@router.get("/orders")
async def orders(request: Request) -> list[dict[str, object]]:
    return [order.model_dump(mode="json") for order in await container(request).broker.get_orders()]


@router.get("/trades")
async def trades(request: Request) -> list[dict[str, object]]:
    """Paper orders joined to their decisions. Every row is simulated."""
    app = container(request)
    rows: list[dict[str, object]] = []
    records = {record.decision_id: record for record in await app.journal.list_recent()}
    for order in await app.broker.get_orders():
        record = records.get(order.request.decision_id)
        rows.append(
            {
                "trade_id": order.broker_order_id,
                "execution_provider": "PaperBroker",
                "is_paper": True,
                "time": order.created_at.isoformat(),
                "symbol": order.request.symbol,
                "side": order.request.side.value,
                "quantity": order.request.quantity,
                "entry_price": order.request.actual_order_value / order.request.quantity,
                "requested_allocation": str(order.request.requested_allocation),
                "actual_order_value": str(order.request.actual_order_value),
                "status": order.status.value,
                "decision_id": order.request.decision_id,
                "ai_action": (
                    record.final_ai_decision.proposals[0].action.value
                    if record is not None
                    and record.final_ai_decision is not None
                    and record.final_ai_decision.proposals
                    else None
                ),
                "thesis": (
                    record.initial_ai_proposal.proposals[0].thesis
                    if record is not None and record.initial_ai_proposal.proposals
                    else None
                ),
            }
        )
    return rows


@router.get("/trades/{trade_id}")
async def trade_detail(request: Request, trade_id: str) -> dict[str, object]:
    for row in await trades(request):
        if row["trade_id"] == trade_id:
            return row
    raise HTTPException(status_code=404, detail="trade not found")


@router.get("/decisions")
async def decisions(request: Request) -> list[dict[str, object]]:
    return [
        record.model_dump(mode="json") for record in await container(request).journal.list_recent()
    ]


@router.get("/ai/decisions/recent")
async def ai_decisions_recent(request: Request) -> list[dict[str, object]]:
    return await decisions(request)


@router.get("/decisions/{decision_id}")
async def decision_detail(request: Request, decision_id: str) -> dict[str, object]:
    record = await container(request).journal.get(decision_id)
    if record is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return record.model_dump(mode="json")


@router.get("/events/recent")
async def recent_events(request: Request, limit: int = 200) -> list[dict[str, Any]]:
    """High-level timeline events, newest last."""
    return container(request).event_hub.recent(limit)


@router.get("/performance")
async def performance(request: Request) -> dict[str, object]:
    """Simple reporting over journalled decisions. No strategy logic."""
    app = container(request)
    records = await app.journal.list_recent()
    account = await app.broker.get_account()

    actions: dict[str, int] = {}
    jev_approvals = 0
    jev_challenges = 0
    changed_after_jev = 0
    retained_after_jev = 0
    ai_latencies: list[int] = []
    jev_latencies: list[int] = []
    executed = 0
    failed = 0
    denied = 0

    for record in records:
        for proposal in record.initial_ai_proposal.proposals:
            key = proposal.action.value
            actions[key] = actions.get(key, 0) + 1
        if record.initial_provider_metadata.latency_ms is not None:
            ai_latencies.append(record.initial_provider_metadata.latency_ms)
        if record.jev_critique is not None:
            if record.jev_critique.agrees:
                jev_approvals += 1
            else:
                jev_challenges += 1
            if record.reconsideration_performed:
                changed_after_jev += 1
            else:
                retained_after_jev += 1
        if (
            record.jev_provider_metadata is not None
            and record.jev_provider_metadata.latency_ms is not None
        ):
            jev_latencies.append(record.jev_provider_metadata.latency_ms)
        if record.orders_generated:
            executed += 1
        elif record.status.value == "FAILED":
            failed += 1
        elif record.status.value == "DENIED_BY_SAFETY":
            denied += 1

    realized = sum((position.realized_pnl for position in account.positions), start=Decimal("0"))
    return {
        "total_pnl": str(realized + account.unrealized_pnl),
        "realized_pnl": str(realized),
        "unrealized_pnl": str(account.unrealized_pnl),
        "cash": str(account.cash),
        "open_positions": len([p for p in account.positions if p.quantity]),
        "total_decisions": len(records),
        "executed": executed,
        "failed": failed,
        "denied_by_safety": denied,
        "actions": actions,
        "jev_approvals": jev_approvals,
        "jev_challenges": jev_challenges,
        "ai_changed_after_jev": changed_after_jev,
        "ai_retained_after_jev": retained_after_jev,
        "avg_ai_latency_ms": _mean(ai_latencies),
        "avg_jev_latency_ms": _mean(jev_latencies),
        "outcome_measurements": [],
    }


def _mean(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


@router.post("/control/review")
async def control_review(request: Request) -> dict[str, object]:
    app = container(request)
    if app.runtime is None:
        raise HTTPException(
            status_code=409, detail="shadow runtime is not running; start the session first"
        )
    result = await app.runtime.review_once()
    app.event_hub.publish(
        new_event("SYSTEM", "review_requested", "Manual AI review requested from UI")
    )
    return {"requested": result is not None, "decision_id": result.record_id if result else None}


@router.post("/control/start")
async def control_start(request: Request) -> dict[str, object]:
    app = container(request)
    if app.runtime is None:
        raise HTTPException(
            status_code=409,
            detail="no shadow runtime is attached to this process; start it with "
            "'ai_trader shadow --provider fyers'",
        )
    await app.runtime.start()
    app.event_hub.publish(new_event("SYSTEM", "session_started", "Shadow review loop started"))
    return {"running": app.runtime.running}


@router.post("/control/stop")
async def control_stop(request: Request) -> dict[str, object]:
    app = container(request)
    if app.runtime is not None:
        await app.runtime.stop()
    app.event_hub.publish(new_event("SYSTEM", "session_stopped", "Shadow review loop stopped"))
    return {"running": False}


@router.post("/control/kill-switch")
async def control_kill_switch(request: Request, enabled: bool = True) -> dict[str, object]:
    app = container(request)
    app.kill_switch.enabled = enabled
    app.event_hub.publish(
        new_event(
            "SAFETY",
            "kill_switch",
            f"Kill switch {'ENGAGED' if enabled else 'released'}",
        )
    )
    return {"kill_switch_state": enabled, "live_order_execution": False}


@router.post("/agent/review")
async def review(request: Request) -> dict[str, object]:
    return await control_review(request)


@router.post("/paper/reset")
async def paper_reset(request: Request) -> dict[str, object]:
    app = container(request)
    app.broker.reset()
    app.event_hub.publish(new_event("PAPER", "paper_reset", "Paper account reset from UI"))
    return {"status": "reset", "execution_provider": "PaperBroker"}


@router.post("/system/kill-switch")
async def kill_switch(request: Request, enabled: bool = True) -> dict[str, bool]:
    container(request).kill_switch.enabled = enabled
    return {"kill_switch_state": enabled}
