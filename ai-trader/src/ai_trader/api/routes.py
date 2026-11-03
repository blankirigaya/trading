"""Read-only and control surfaces for the monitoring UI.

Handlers stay thin: they read container state or delegate to the shadow runtime.
No orchestration, market logic, or trading judgment lives in this module.
"""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ai_trader.api.events import new_event
from ai_trader.application.container import Container
from ai_trader.application.fyers_session import ShadowSession, within_nse_hours
from ai_trader.application.runtime import ManagedShadowRuntime
from ai_trader.application.universe import normalize_mode, resolve_master
from ai_trader.brokers.paper import PaperBroker
from ai_trader.domain.decisions import PortfolioAction
from ai_trader.domain.orders import Fill, Order
from ai_trader.domain.portfolio import PortfolioState, Position
from ai_trader.journal.service import DecisionRecord
from ai_trader.persistence.trade_ledger import TradeLedger
from ai_trader.reporting.usage import summarize_usage

router = APIRouter()


def _brokers(app: Container) -> list[PaperBroker]:
    """Live paper books: one per reviewer shard, else the container default."""
    return list(app.shard_brokers) if app.shard_brokers else [app.broker]


def _session(app: Container) -> ShadowSession | None:
    runtime = app.runtime
    return runtime.session if isinstance(runtime, ManagedShadowRuntime) else None


def _ticker(full_symbol: str) -> str:
    """``NSE:INFY-EQ`` -> ``INFY`` for the UI."""
    return full_symbol.split(":", 1)[-1].rsplit("-", 1)[0]


def _watched_symbols(app: Container) -> list[str]:
    """Tickers the AI currently watches, falling back to the static config."""
    session = _session(app)
    if session is not None:
        if session.universe_selected:
            return [_ticker(symbol) for symbol in session.universe_selected]
        if session.shards:
            return [_ticker(symbol) for shard in session.shards for symbol in shard.symbols]
    return list(app.settings.shadow_symbols)


async def _combined_portfolio(app: Container) -> PortfolioState:
    """Aggregate all shard paper books into one combined portfolio for the UI."""
    cash = reserved = realized = unrealized = Decimal("0")
    positions: list[Position] = []
    for broker in _brokers(app):
        account = await broker.get_account()
        cash += account.cash
        reserved += account.reserved_cash
        realized += account.realized_pnl
        unrealized += account.unrealized_pnl
        positions.extend(account.positions)
    return PortfolioState(
        cash=cash,
        reserved_cash=reserved,
        positions=positions,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
    )


async def _combined_equity(app: Container) -> Decimal:
    """Net worth across the shard books: cash + blocked margin + unrealized P&L.

    A paper BUY deducts only ``value / margin_multiple`` from cash, so the
    notional cost of an open position is not all gone from cash. Reporting cash
    as the portfolio value therefore understates it by the margin still tied up
    in positions; equity is the money actually at work plus its P&L.
    """
    total = Decimal("0")
    for broker in _brokers(app):
        account = await broker.get_account()
        blocked = sum(
            (position.average_entry_price * position.quantity for position in account.positions),
            Decimal("0"),
        )
        total += account.cash + account.unrealized_pnl + blocked / broker.margin_multiple
    return total


async def _charges_paid(app: Container) -> Decimal:
    """Total charges booked against closed paper round trips (all shards)."""
    engine = getattr(app.journal, "engine", None)
    if engine is None:
        return Decimal("0")
    return await TradeLedger(engine).total_charges()


def container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


# Trade decisions name a stock the shard actually acted on. HOLD / STAY_IN_CASH
# reviews are journaled under the shard's primary tick, so they would fill every
# bot's list with the same repeated symbol; the Bots page shows trades only.
_TRADE_ACTIONS = frozenset(
    {
        PortfolioAction.OPEN_POSITION.value,
        PortfolioAction.INCREASE_POSITION.value,
        PortfolioAction.REDUCE_POSITION.value,
        PortfolioAction.CLOSE_POSITION.value,
    }
)


def _record_action(record: DecisionRecord) -> str | None:
    """First proposal action of the final decision, falling back to the initial."""
    final = record.final_ai_decision
    if final is not None and final.proposals:
        return final.proposals[0].action.value
    if record.initial_ai_proposal.proposals:
        return record.initial_ai_proposal.proposals[0].action.value
    return None


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/system/status")
async def system_status(request: Request) -> dict[str, object]:
    app = container(request)
    session = _session(app)
    universe_mode = session.universe_mode if session is not None else await app.get_universe_mode()
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
        "shadow_symbols": _watched_symbols(app),
        "universe_mode": universe_mode,
        "universe_size": app.settings.universe_size,
        "universe_master_size": len(resolve_master(app.settings)),
        "universe_selected": (
            [_ticker(symbol) for symbol in session.universe_selected]
            if session is not None
            else []
        ),
        "capital_cap_disabled": (
            session.capital_cap_disabled
            if session is not None
            else await app.get_capital_cap_disabled()
        ),
        "position_count_cap_disabled": (
            session.position_count_cap_disabled
            if session is not None
            else await app.get_position_count_cap_disabled()
        ),
        "tier1_news_enabled": (
            session.tier1_news_enabled
            if session is not None
            else await app.get_tier1_news_enabled()
        ),
        "news_analysis_enabled": (
            session.news_analysis_enabled
            if session is not None
            else await app.get_news_analysis_enabled()
        ),
        "sonnet_escalation_enabled": (
            session.sonnet_escalation_enabled
            if session is not None
            else await app.get_sonnet_escalation_enabled()
        ),
        "sonnet_escalations_today": session.sonnet_escalations_today if session is not None else 0,
        "ai_max_sonnet_escalations_per_day": app.settings.ai_max_sonnet_escalations_per_day,
        "shadow_initial_capital_inr": str(app.settings.shadow_initial_capital_inr),
        "max_ai_capital_inr": str(app.settings.max_ai_capital_inr),
        "max_single_position_inr": str(app.settings.max_single_position_inr),
        "max_gross_exposure_inr": str(app.settings.max_gross_exposure_inr),
        "max_open_positions": app.settings.max_open_positions,
        "portfolio_review_interval_seconds": app.settings.portfolio_review_interval_seconds,
        "database": "connected",
        "news_enabled": app.settings.news_enabled,
        # Cached provider health. This endpoint never performs an inference request.
        **app.ai_health.snapshot(),
    }


@router.get("/market/status")
async def market_status(request: Request) -> dict[str, object]:
    app = container(request)
    feed_idle_seconds: float | None = None
    socket_reconnects: int | None = None
    runtime = app.runtime
    session = runtime.session if isinstance(runtime, ManagedShadowRuntime) else None
    if session is not None:
        feed_idle_seconds = session.feed.service.idle_seconds(app.clock.now())
        socket_reconnects = session.feed.socket.reconnect_count
    return {
        "provider": app.settings.market_provider,
        "connected": app.market_connected,
        "symbols": _watched_symbols(app),
        "feed_mode": app.settings.fyers_feed_mode,
        "fyers_market_data_only": True,
        "feed_idle_seconds": feed_idle_seconds,
        "socket_reconnects": socket_reconnects,
        # Freshness of the snapshots the UI actually renders. Distinct from
        # feed_idle_seconds: this is what turns an amber banner when data is
        # stale even though the HTTP API and socket are both up.
        "snapshot_age_seconds": app.market_snapshot_age_seconds(),
        # The UI only warns about stale prices while the exchange is open;
        # overnight silence is normal closure, not a fault.
        "market_open": within_nse_hours(app.clock.now()),
    }


@router.get("/market/snapshots")
async def market_snapshots(request: Request) -> list[dict[str, object]]:
    """Latest normalized market state per instrument, as produced by the feed."""
    return container(request).market_snapshots()


@router.get("/market/candles")
async def market_candles(request: Request, symbol: str) -> list[dict[str, object]]:
    return container(request).market_candles(symbol)


@router.get("/bots")
async def bots(request: Request) -> list[dict[str, object]]:
    """One entry per AI reviewer shard: its symbols, paper book, and recent decisions.

    Empty when no shadow session is attached. Read-only; the UI never drives
    trading through this endpoint.
    """
    app = container(request)
    runtime = app.runtime
    session = runtime.session if isinstance(runtime, ManagedShadowRuntime) else None
    if session is None or not session.shards:
        return []
    price_by_symbol: dict[str, object] = {}
    for snapshot in app.market_snapshots():
        instrument = snapshot.get("instrument")
        if isinstance(instrument, dict):
            symbol = instrument.get("symbol")
            if isinstance(symbol, str):
                price_by_symbol[symbol] = snapshot.get("last_price")
    records = await app.journal.list_recent(limit=200)
    bots: list[dict[str, object]] = []
    for shard in session.shards:
        # Shard symbols use FYERS form ("NSE:INFY-EQ"); journaled decisions carry tickers.
        tickers = [full.split(":", 1)[-1].rsplit("-", 1)[0] for full in shard.symbols]
        tickerset = {ticker.upper() for ticker in tickers}
        account = await shard.broker.get_account()
        positions: list[dict[str, object]] = []
        for position in account.positions:
            if not position.quantity:
                continue
            ltp = price_by_symbol.get(position.symbol, position.average_entry_price)
            price = Decimal(str(ltp))
            positions.append(
                {
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "average_entry_price": str(position.average_entry_price),
                    "ltp": str(price),
                    "value": str(price * position.quantity),
                    "unrealized_pnl": str(
                        (price - position.average_entry_price) * position.quantity
                    ),
                }
            )
        shard_records = [
            record
            for record in records
            if (record.symbol or "").upper() in tickerset
            and _record_action(record) in _TRADE_ACTIONS
        ][:5]
        recent = []
        for record in shard_records:
            action = _record_action(record)
            recent.append(
                {
                    "decision_id": record.decision_id,
                    "symbol": record.symbol,
                    "status": record.status.value,
                    "timestamp": record.timestamp.isoformat(),
                    "ai_action": action,
                }
            )
        bots.append(
            {
                "index": shard.index,
                "name": f"Bot {shard.index + 1}",
                "symbols": sorted(tickers),
                "cash": str(account.cash),
                "positions": positions,
                "reviews": shard.reviews,
                "ai_model": shard.provider.model_name,
                "recent_decisions": recent,
            }
        )
    return bots


@router.get("/charges/schedule")
async def charges_schedule(request: Request) -> dict[str, object]:
    """Active paper charge schedule so the UI can estimate exit costs.

    Read-only reporting: rates come from settings, never from clients. The
    holdings table subtracts this estimate from unrealized P&L so green paint
    is never mistaken for bankable profit.
    """
    from ai_trader.brokers.charges import charge_rates_for

    rates = charge_rates_for(container(request).settings)
    if rates is None:
        return {"enabled": False, "rates": None}
    return {
        "enabled": True,
        "rates": {
            "brokerage_pct": str(rates.brokerage_pct),
            "brokerage_cap_inr": str(rates.brokerage_cap_inr),
            "stt_sell_pct": str(rates.stt_sell_pct),
            "exchange_pct": str(rates.exchange_pct),
            "sebi_pct": str(rates.sebi_pct),
            "stamp_duty_buy_pct": str(rates.stamp_duty_buy_pct),
            "gst_pct": str(rates.gst_pct),
        },
    }


@router.get("/portfolio")
async def portfolio(request: Request) -> dict[str, object]:
    app = container(request)
    combined = await _combined_portfolio(app)
    payload = combined.model_dump(mode="json")
    payload["equity"] = str(await _combined_equity(app))
    charges = await _charges_paid(app)
    payload["charges_paid"] = str(charges)
    # Realized P&L is net of charges; gross adds them back for a like-for-like
    # view of trading skill. Historical trades have zero recorded charges.
    payload["gross_realized_pnl"] = str(combined.realized_pnl + charges)
    return payload


@router.get("/shadow/portfolio")
async def shadow_portfolio(request: Request) -> dict[str, object]:
    return await portfolio(request)


@router.get("/positions")
async def positions(request: Request) -> list[dict[str, object]]:
    app = container(request)
    out: list[dict[str, object]] = []
    for broker in _brokers(app):
        out.extend(
            position.model_dump(mode="json") for position in (await broker.get_positions()).positions
        )
    return out


@router.get("/orders")
async def orders(request: Request) -> list[dict[str, object]]:
    app = container(request)
    all_orders = [order for broker in _brokers(app) for order in await broker.get_orders()]
    all_orders.sort(key=lambda order: order.created_at, reverse=True)
    return [order.model_dump(mode="json") for order in all_orders]


@router.get("/trades")
async def trades(request: Request) -> list[dict[str, object]]:
    """Paper orders joined to their decisions. Every row is simulated."""
    app = container(request)
    rows: list[dict[str, object]] = []
    records = {record.decision_id: record for record in await app.journal.list_recent(limit=500)}
    orders: list[Order] = []
    fills: dict[str, Fill] = {}
    for broker in _brokers(app):
        orders.extend(await broker.get_orders())
        for broker_fill in broker.fills:
            fills[broker_fill.broker_order_id] = broker_fill
    orders.sort(key=lambda order: order.created_at, reverse=True)
    for order in orders:
        record = records.get(order.request.decision_id)
        fill = fills.get(order.broker_order_id)
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
                "fees": str(fill.fees) if fill is not None else "0",
                "entry_fees": str(fill.entry_fees) if fill is not None else "0",
                "charge_breakdown": (
                    {key: str(value) for key, value in fill.charge_breakdown.items()}
                    if fill is not None
                    else {}
                ),
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


@router.get("/trades/history")
async def trades_history(request: Request, limit: int = 300) -> dict[str, object]:
    """Permanent closed-trade record with realized rupee P&L per round trip.

    Backed by the SQLite ledger, so it survives backend restarts — unlike the
    in-memory open books. Newest first.
    """
    from ai_trader.persistence.trade_ledger import TradeLedger

    app = container(request)
    engine = getattr(app.journal, "engine", None)
    if engine is None:
        return {
            "closed_trades": [],
            "total_realized_pnl": "0",
            "total_charges": "0",
            "gross_realized_pnl": "0",
            "wins": 0,
            "losses": 0,
        }
    trades = await TradeLedger(engine).closed_trades(limit=limit)
    total = sum((trade.realized_pnl for trade in trades), Decimal("0"))
    total_charges = sum((trade.charges for trade in trades), Decimal("0"))
    return {
        "closed_trades": [
            {
                "closed_trade_id": trade.closed_trade_id,
                "shard": trade.shard,
                "symbol": trade.symbol,
                "quantity": trade.quantity,
                "entry_price": str(trade.entry_price),
                "exit_price": str(trade.exit_price),
                # realized_pnl is net of both legs; gross adds charges back.
                "realized_pnl": str(trade.realized_pnl),
                "charges": str(trade.charges),
                "gross_pnl": str(trade.realized_pnl + trade.charges),
                "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
                "closed_at": trade.closed_at.isoformat(),
                "entry_decision_id": trade.entry_decision_id,
                "exit_decision_id": trade.exit_decision_id,
            }
            for trade in trades
        ],
        "total_realized_pnl": str(total),
        "total_charges": str(total_charges),
        "gross_realized_pnl": str(total + total_charges),
        "wins": sum(1 for trade in trades if trade.realized_pnl > 0),
        "losses": sum(1 for trade in trades if trade.realized_pnl < 0),
    }


@router.get("/trades/{trade_id}")
async def trade_detail(request: Request, trade_id: str) -> dict[str, object]:
    for row in await trades(request):
        if row["trade_id"] == trade_id:
            return row
    raise HTTPException(status_code=404, detail="trade not found")


@router.get("/decisions")
async def decisions(request: Request, limit: int = 300) -> list[dict[str, object]]:
    """Newest journal records first, bounded: the full journal grows without
    bound and serializing all of it on every 10s poll saturates the loop."""
    return [
        record.model_dump(mode="json")
        for record in await container(request).journal.list_recent(limit=limit)
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
    account = await _combined_portfolio(app)

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

    # Realized P&L lives on the book, not on the positions it came from: a fully
    # closed position leaves the open book, so summing position rows would
    # report 0 after every round trip. It is net of charges; gross adds them back.
    realized = account.realized_pnl
    charges = await _charges_paid(app)
    return {
        "total_pnl": str(realized + account.unrealized_pnl),
        "realized_pnl": str(realized),
        "gross_realized_pnl": str(realized + charges),
        "charges_paid": str(charges),
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
        "ai_usage": summarize_usage(
            records,
            app.news_store().recent_analyses(limit=5000),
            cost_configured=(
                app.settings.bedrock_input_usd_per_million_tokens is not None
                or app.settings.bedrock_output_usd_per_million_tokens is not None
            ),
        ),
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
    await app.attach_runtime()
    assert app.runtime is not None
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


@router.post("/control/universe")
async def control_universe(request: Request, mode: str) -> dict[str, object]:
    """Select the AI's review universe. Persisted; applied at the next review.

    The switch is deliberately deferred: resubscribing the FYERS feed mid-review
    would blank the board under an in-flight decision. Held symbols stay on the
    feed and are always closable, whatever mode is active.
    """
    app = container(request)
    try:
        normalized = normalize_mode(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    applied = await app.set_universe_mode(normalized)
    session = _session(app)
    if session is not None:
        session.universe_mode = applied
    app.event_hub.publish(
        new_event(
            "SYSTEM",
            "universe_changed",
            f"AI universe set to {applied.upper()}; applies at the next review",
            data={"universe_mode": applied},
        )
    )
    return {"universe_mode": applied, "applies": "next_review"}


@router.post("/control/capital-cap")
async def control_capital_cap(request: Request, disabled: bool = True) -> dict[str, object]:
    """Lift or restore the fixed AI money caps. Persisted and applied immediately.

    Disabling removes MAX_AI_CAPITAL_INR, MAX_SINGLE_POSITION_INR, and
    MAX_GROSS_EXPOSURE_INR from new buys only; exits stay executable and paper
    cash remains the real limit. The max open positions cap still applies
    unless it is separately disabled.
    """
    app = container(request)
    applied = await app.set_capital_cap_disabled(disabled)
    app.event_hub.publish(
        new_event(
            "SAFETY",
            "capital_cap",
            f"AI capital cap {'DISABLED' if applied else 'ENABLED'}; "
            + (
                "the AI sizes within available paper cash"
                if applied
                else "fixed money caps apply to new buys"
            ),
            data={"capital_cap_disabled": applied},
        )
    )
    return {"capital_cap_disabled": applied, "live_order_execution": False}


@router.post("/control/position-cap")
async def control_position_cap(request: Request, disabled: bool = True) -> dict[str, object]:
    """Lift or restore the max-open-positions cap. Persisted and applied immediately.

    Disabling removes MAX_OPEN_POSITIONS from new symbols only; adding to a
    symbol already held is never gated by the count, exits stay executable,
    and paper cash remains the real limit.
    """
    app = container(request)
    applied = await app.set_position_count_cap_disabled(disabled)
    app.event_hub.publish(
        new_event(
            "SAFETY",
            "position_cap",
            f"Max open positions cap {'DISABLED' if applied else 'ENABLED'}; "
            + (
                "the AI sizes the number of positions within available paper cash"
                if applied
                else f"new symbols limited to {app.settings.max_open_positions} open positions"
            ),
            data={"position_count_cap_disabled": applied},
        )
    )
    return {"position_count_cap_disabled": applied, "live_order_execution": False}


@router.post("/control/tier1-news")
async def control_tier1_news(request: Request, enabled: bool = True) -> dict[str, object]:
    """Turn the Tier-1 desks (CNBC-TV18, NDTV Profit, Business Today) on or off.

    Persisted and applied immediately to the running ingestion: disabling
    stops those feeds' polling tasks, enabling restarts them. Official
    filings and secondary dailies are unaffected.
    """
    app = container(request)
    applied = await app.set_tier1_news_enabled(enabled)
    app.event_hub.publish(
        new_event(
            "SYSTEM",
            "tier1_news",
            f"Tier-1 news desks {'ENABLED' if applied else 'DISABLED'}",
            data={"tier1_news_enabled": applied},
        )
    )
    return {"tier1_news_enabled": applied, "live_order_execution": False}


@router.post("/control/news-analysis")
async def control_news_analysis(request: Request, enabled: bool = True) -> dict[str, object]:
    """Turn AI analysis of news items on or off. Persisted, applied immediately.

    When off, headlines still ingest and deduplicate, but no Sonnet analysis
    runs, so nothing new can trigger a news review either. Cadence reviews
    are unaffected.
    """
    app = container(request)
    applied = await app.set_news_analysis_enabled(enabled)
    app.event_hub.publish(
        new_event(
            "SYSTEM",
            "news_analysis",
            f"News AI analysis {'ENABLED' if applied else 'DISABLED'}",
            data={"news_analysis_enabled": applied},
        )
    )
    return {"news_analysis_enabled": applied, "live_order_execution": False}


@router.post("/control/sonnet-escalation")
async def control_sonnet_escalation(
    request: Request, enabled: bool = True
) -> dict[str, object]:
    """Turn the Sonnet escalation lever on or off. Persisted, applied immediately.

    When on, news-triggered reviews run on Sonnet (bounded by the daily cap);
    when off, they run on the cadence model like everything else.
    """
    app = container(request)
    applied = await app.set_sonnet_escalation_enabled(enabled)
    app.event_hub.publish(
        new_event(
            "SYSTEM",
            "sonnet_escalation",
            f"Sonnet news escalation {'ENABLED' if applied else 'DISABLED'}",
            data={"sonnet_escalation_enabled": applied},
        )
    )
    return {"sonnet_escalation_enabled": applied, "live_order_execution": False}


@router.post("/agent/review")
async def review(request: Request) -> dict[str, object]:
    return await control_review(request)


@router.post("/paper/reset")
async def paper_reset(request: Request) -> dict[str, object]:
    app = container(request)
    for broker in _brokers(app):
        broker.reset()
        await broker.clear_ledger_book()
    app.event_hub.publish(new_event("PAPER", "paper_reset", "Paper accounts reset from UI"))
    return {"status": "reset", "execution_provider": "PaperBroker"}


@router.post("/system/kill-switch")
async def kill_switch(request: Request, enabled: bool = True) -> dict[str, bool]:
    container(request).kill_switch.enabled = enabled
    return {"kill_switch_state": enabled}
