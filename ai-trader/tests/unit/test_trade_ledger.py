"""Permanent paper-trade ledger: fills, closed P&L, and restart recovery."""

from decimal import Decimal

from sqlalchemy import create_engine

from ai_trader.brokers.paper import PaperBroker
from ai_trader.domain.common import SystemClock
from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType
from ai_trader.persistence.trade_ledger import TradeLedger
from ai_trader.persistence.trade_models import ensure_trade_tables


def _request(symbol: str, side: OrderSide, quantity: int, decision: str) -> OrderRequest:
    return OrderRequest(
        client_order_id=f"c-{symbol}-{side.value}-{quantity}",
        decision_id=decision,
        correlation_id="corr",
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        limit_price=None,
        requested_allocation=Decimal("100000"),
        actual_order_value=Decimal("100000"),
        conversion_reason="test",
        product="INTRADAY",
        validity="DAY",
    )


def _broker(tmp_path, **overrides):
    from pathlib import Path

    engine = create_engine(f"sqlite:///{Path(tmp_path) / 'ledger.db'}")
    ensure_trade_tables(engine)
    clock = SystemClock()
    params = dict(
        initial_cash=Decimal("33334"),
        clock=clock,
        prices={"INFY": Decimal("100")},
        margin_multiple=Decimal("5"),
        shard=0,
        ledger=TradeLedger(engine),
    )
    params.update(overrides)
    return PaperBroker(**params), engine


async def test_buy_persists_and_restores_across_restart(tmp_path) -> None:
    broker, engine = _broker(tmp_path)
    broker.set_price("INFY", Decimal("100"))
    await broker.place_order(_request("INFY", OrderSide.BUY, 10, "d1"))

    fresh, _ = _broker(tmp_path)
    fresh.ledger = TradeLedger(engine)
    await fresh.restore()

    assert fresh.cash == broker.cash
    assert fresh.positions["INFY"].quantity == 10
    assert fresh.positions["INFY"].average_entry_price == Decimal("100")


async def test_order_ids_do_not_collide_after_restart(tmp_path) -> None:
    broker, engine = _broker(tmp_path)
    broker.set_price("INFY", Decimal("100"))
    first = await broker.place_order(_request("INFY", OrderSide.BUY, 10, "d1"))

    fresh, _ = _broker(tmp_path)
    fresh.ledger = TradeLedger(engine)
    await fresh.restore()
    fresh.set_price("INFY", Decimal("100"))
    second = await fresh.place_order(_request("INFY", OrderSide.BUY, 5, "d2"))

    assert first.broker_order_id != second.broker_order_id


async def test_sell_records_closed_trade_with_pnl(tmp_path) -> None:
    broker, engine = _broker(tmp_path)
    broker.set_price("INFY", Decimal("100"))
    await broker.place_order(_request("INFY", OrderSide.BUY, 10, "d1"))
    broker.set_price("INFY", Decimal("110"))
    await broker.place_order(_request("INFY", OrderSide.SELL, 10, "d2"))

    ledger = TradeLedger(engine)
    closed = await ledger.closed_trades()
    assert len(closed) == 1
    trade = closed[0]
    assert trade.symbol == "INFY"
    assert trade.quantity == 10
    assert trade.entry_price == Decimal("100")
    assert trade.exit_price == Decimal("110")
    assert trade.realized_pnl == Decimal("100")
    assert trade.exit_decision_id == "d2"
    assert trade.entry_decision_id == "d1"

    # The position is flat but its history survives a restart.
    fresh, _ = _broker(tmp_path)
    fresh.ledger = TradeLedger(engine)
    await fresh.restore()
    assert fresh.positions == {}
    assert (await TradeLedger(engine).closed_trades())[0].realized_pnl == Decimal("100")
    # Realized P&L is read from the close history, not from the now-gone
    # position row, so the book must still report it after a restart.
    assert broker.realized_pnl == Decimal("100")
    assert await TradeLedger(engine).realized_pnl(0) == Decimal("100")
    assert fresh.realized_pnl == Decimal("100")
    assert (await fresh.get_account()).realized_pnl == Decimal("100")


async def test_reset_keeps_history_but_forgets_open_book(tmp_path) -> None:
    broker, engine = _broker(tmp_path)
    broker.set_price("INFY", Decimal("100"))
    await broker.place_order(_request("INFY", OrderSide.BUY, 10, "d1"))
    broker.reset()
    await broker.clear_ledger_book()

    assert broker.cash == Decimal("33334")
    assert broker.positions == {}
    ledger = TradeLedger(engine)
    cash, positions = await ledger.load_book(0)
    assert cash is None
    assert positions == []


async def test_ledger_without_broker_attachment_is_inert(tmp_path) -> None:
    from ai_trader.brokers.paper import PaperBroker as PB

    plain = PB(Decimal("1000"), SystemClock(), {"T": Decimal("10")})
    await plain.restore()
    assert plain.cash == Decimal("1000")


def test_ensure_trade_tables_on_fresh_db(tmp_path) -> None:
    from pathlib import Path

    from sqlalchemy import inspect

    engine = create_engine(f"sqlite:///{Path(tmp_path) / 'fresh.db'}")
    ensure_trade_tables(engine)
    ensure_trade_tables(engine)
    assert set(inspect(engine).get_table_names()) >= {
        "paper_fills",
        "paper_positions",
        "paper_accounts",
        "paper_closed_trades",
    }
