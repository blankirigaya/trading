"""Cash-based affordability and exit robustness.

Two rules this pins:
* a BUY is allowed while the *available cash* covers its margin, independent of
  how much gross notional the book already carries;
* a CLOSE with no share count still executes at the held quantity, because a
  requested exit must never be silently dropped.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.decisions import PortfolioAction, TradeProposal
from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType
from ai_trader.domain.portfolio import PortfolioState, Position
from ai_trader.execution.service import ExecutionAdjustmentRequired, ExecutionService
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def _order(value: str, allocation: str = "24000") -> OrderRequest:
    return OrderRequest(
        client_order_id="c1",
        decision_id="d1",
        correlation_id="corr",
        symbol="INFY",
        exchange="NSE",
        segment="EQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        requested_allocation=Decimal(allocation),
        actual_order_value=Decimal(value),
        conversion_reason="test",
    )


def _envelope(clock, **overrides) -> SafetyEnvelope:
    arguments = {
        "trading_enabled": True,
        "paper_margin_multiple": Decimal("5"),
        "max_ai_capital_inr": Decimal("50000"),
        "max_single_position_inr": Decimal("25000"),
        "max_gross_exposure_inr": Decimal("0"),
    }
    arguments.update(overrides)
    settings = Settings(**arguments)
    return SafetyEnvelope(settings, KillSwitch(), clock)


def test_buy_is_allowed_when_cash_covers_margin_even_with_large_gross(clock) -> None:
    envelope = _envelope(clock)
    portfolio = PortfolioState(
        cash=Decimal("10000"),
        positions=[
            Position(symbol="TCS", quantity=100, average_entry_price=Decimal("1000"))
        ],
    )
    # 24,000 order needs 4,800 margin; the book already carries 100,000 gross.
    assert envelope.check(_order("24000"), portfolio).allowed is True


def test_buy_is_denied_when_cash_cannot_cover_margin(clock) -> None:
    envelope = _envelope(clock)
    portfolio = PortfolioState(cash=Decimal("4000"))
    result = envelope.check(_order("24000"), portfolio)
    assert result.allowed is False
    assert "Insufficient available cash" in (result.reason or "")


def test_positive_gross_cap_still_applies(clock) -> None:
    envelope = _envelope(clock, max_gross_exposure_inr=Decimal("50000"))
    portfolio = PortfolioState(
        cash=Decimal("100000"),
        positions=[
            Position(symbol="TCS", quantity=45, average_entry_price=Decimal("1000"))
        ],
    )
    result = envelope.check(_order("24000"), portfolio)
    assert result.allowed is False
    assert "MAX_GROSS_EXPOSURE_INR" in (result.reason or "")


def test_close_without_quantity_uses_the_held_quantity(clock) -> None:
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("100")})
    broker.positions["INFY"] = Position(
        symbol="INFY", quantity=7, average_entry_price=Decimal("100")
    )
    proposal = TradeProposal(
        proposal_id="x",
        symbol="INFY",
        action=PortfolioAction.CLOSE_POSITION,
        side=OrderSide.SELL,
        quantity_shares=None,
        confidence=0.8,
        thesis="exit",
    )

    request = ExecutionService(broker).prepare("d1", "c1", proposal, Decimal("110"))

    assert request.quantity == 7
    assert request.side is OrderSide.SELL


def test_reduce_still_requires_an_explicit_quantity(clock) -> None:
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("100")})
    broker.positions["INFY"] = Position(
        symbol="INFY", quantity=7, average_entry_price=Decimal("100")
    )
    proposal = TradeProposal(
        proposal_id="x",
        symbol="INFY",
        action=PortfolioAction.REDUCE_POSITION,
        side=OrderSide.SELL,
        quantity_shares=None,
        confidence=0.8,
        thesis="trim",
    )

    with pytest.raises(ExecutionAdjustmentRequired):
        ExecutionService(broker).prepare("d1", "c1", proposal, Decimal("110"))
