"""Zerodha equity-intraday charges on paper fills.

The paper book used to fill for free. These tests pin the statutory breakdown,
the two-leg round trip, partial fills, and persistence so a restart cannot
silently forget what a position already cost to open.
"""

from decimal import Decimal

from sqlalchemy import create_engine, inspect, text

from ai_trader.brokers.charges import (
    ChargeBreakdown,
    ChargeRates,
    charge_rates_for,
    compute_charges,
)
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import SystemClock
from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType
from ai_trader.persistence.trade_ledger import TradeLedger
from ai_trader.persistence.trade_models import ensure_trade_tables


def _request(symbol: str, side: OrderSide, quantity: int, decision: str) -> OrderRequest:
    return OrderRequest(
        client_order_id=f"c-{symbol}-{side.value}-{quantity}-{decision}",
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
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    ensure_trade_tables(engine)
    params = dict(
        initial_cash=Decimal("33334"),
        clock=SystemClock(),
        prices={"INFY": Decimal("100")},
        margin_multiple=Decimal("5"),
        shard=0,
        ledger=TradeLedger(engine),
        charge_rates=ChargeRates(),
    )
    params.update(overrides)
    return PaperBroker(**params), engine


def test_buy_breakdown_matches_zerodha_schedule() -> None:
    breakdown = compute_charges(OrderSide.BUY, Decimal("1000"))
    assert breakdown.brokerage == Decimal("0.30")
    assert breakdown.exchange == Decimal("0.03")
    assert breakdown.sebi == Decimal("0.00")
    assert breakdown.stt == Decimal("0.00")
    assert breakdown.stamp_duty == Decimal("0.03")
    assert breakdown.gst == Decimal("0.06")
    assert breakdown.total == Decimal("0.42")


def test_sell_breakdown_charges_stt_and_no_stamp() -> None:
    breakdown = compute_charges(OrderSide.SELL, Decimal("1100"))
    assert breakdown.brokerage == Decimal("0.33")
    assert breakdown.stt == Decimal("0.28")
    assert breakdown.exchange == Decimal("0.03")
    assert breakdown.sebi == Decimal("0.00")
    assert breakdown.stamp_duty == Decimal("0.00")
    assert breakdown.gst == Decimal("0.06")
    assert breakdown.total == Decimal("0.70")


def test_brokerage_is_capped_at_twenty_rupees() -> None:
    breakdown = compute_charges(OrderSide.BUY, Decimal("1000000"))
    assert breakdown.brokerage == Decimal("20.00")


def test_zero_turnover_costs_nothing() -> None:
    assert compute_charges(OrderSide.BUY, Decimal("0")) == ChargeBreakdown()


def test_charges_can_be_disabled() -> None:
    assert charge_rates_for(Settings(paper_charges_enabled=False)) is None
    enabled = charge_rates_for(Settings(paper_charges_enabled=True))
    assert enabled == ChargeRates()


async def test_round_trip_realized_pnl_is_net_of_both_legs(tmp_path) -> None:
    broker, engine = _broker(tmp_path)
    await broker.place_order(_request("INFY", OrderSide.BUY, 10, "d1"))
    assert broker.entry_charges["INFY"] == Decimal("0.42")

    broker.set_price("INFY", Decimal("110"))
    await broker.place_order(_request("INFY", OrderSide.SELL, 10, "d2"))

    # Gross 100, entry 0.42 + exit 0.70.
    assert broker.realized_pnl == Decimal("98.88")
    assert broker.cash == Decimal("33334") + Decimal("98.88")
    assert broker.entry_charges == {}

    trade = (await TradeLedger(engine).closed_trades())[0]
    assert trade.realized_pnl == Decimal("98.88")
    assert trade.charges == Decimal("1.12")
    assert trade.realized_pnl + trade.charges == Decimal("100.00")


async def test_partial_sell_releases_proportional_entry_charges(tmp_path) -> None:
    broker, _ = _broker(tmp_path)
    await broker.place_order(_request("INFY", OrderSide.BUY, 10, "d1"))
    broker.set_price("INFY", Decimal("110"))
    await broker.place_order(_request("INFY", OrderSide.SELL, 5, "d2"))

    # Half the 0.42 entry cost is released; exit fees on 550 are 0.36.
    assert broker.entry_charges["INFY"] == Decimal("0.21")
    assert broker.realized_pnl == Decimal("50") - Decimal("0.36") - Decimal("0.21")
    assert broker.positions["INFY"].quantity == 5


async def test_entry_charges_and_breakdown_survive_restart(tmp_path) -> None:
    broker, engine = _broker(tmp_path)
    await broker.place_order(_request("INFY", OrderSide.BUY, 10, "d1"))

    fresh, _ = _broker(tmp_path)
    fresh.ledger = TradeLedger(engine)
    await fresh.restore()
    assert fresh.entry_charges["INFY"] == Decimal("0.42")

    fresh.set_price("INFY", Decimal("110"))
    await fresh.place_order(_request("INFY", OrderSide.SELL, 10, "d2"))
    assert fresh.realized_pnl == Decimal("98.88")
    assert await TradeLedger(engine).total_charges() == Decimal("1.12")


async def test_fill_rows_store_the_charge_breakdown(tmp_path) -> None:
    broker, engine = _broker(tmp_path)
    await broker.place_order(_request("INFY", OrderSide.BUY, 10, "d1"))

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT fee, entry_fees, brokerage, stt, exchange_charge, "
                "sebi_charge, stamp_duty, gst FROM paper_fills"
            )
        ).one()
    assert Decimal(str(row.fee)) == Decimal("0.42")
    assert Decimal(str(row.entry_fees)) == Decimal("0")
    assert Decimal(str(row.brokerage)) == Decimal("0.30")
    assert Decimal(str(row.stt)) == Decimal("0")
    assert Decimal(str(row.exchange_charge)) == Decimal("0.03")
    assert Decimal(str(row.stamp_duty)) == Decimal("0.03")
    assert Decimal(str(row.gst)) == Decimal("0.06")


def test_existing_database_gains_charge_columns(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE paper_fills ("
                "fill_id VARCHAR(64) PRIMARY KEY, shard INTEGER, "
                "broker_order_id VARCHAR(64), decision_id VARCHAR(64), "
                "symbol VARCHAR(40), side VARCHAR(8), quantity INTEGER, "
                "price NUMERIC(18,4), fee NUMERIC(18,4), "
                "cash_after NUMERIC(18,4), filled_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO paper_fills VALUES "
                "('F1', 0, 'P1', 'D1', 'INFY', 'BUY', 1, 100, 0, 900, '2026-01-01')"
            )
        )

    ensure_trade_tables(engine)
    ensure_trade_tables(engine)  # idempotent

    columns = {column["name"] for column in inspect(engine).get_columns("paper_fills")}
    assert {"entry_fees", "brokerage", "stt", "exchange_charge", "gst"} <= columns
    with engine.connect() as connection:
        old = connection.execute(
            text("SELECT fee, brokerage, entry_fees FROM paper_fills WHERE fill_id = 'F1'")
        ).one()
    assert Decimal(str(old.fee)) == Decimal("0")
    assert Decimal(str(old.brokerage)) == Decimal("0")
    assert Decimal(str(old.entry_fees)) == Decimal("0")
