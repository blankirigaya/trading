"""Exits must not require a budget allocation: closing frees capital."""

from decimal import Decimal

import pytest

from ai_trader.brokers.paper import PaperBroker
from ai_trader.domain.common import SystemClock
from ai_trader.domain.decisions import PortfolioAction, TradeProposal
from ai_trader.domain.orders import OrderSide
from ai_trader.execution.service import ExecutionAdjustmentRequired, ExecutionService


def _proposal(action: PortfolioAction, **overrides) -> TradeProposal:
    fields: dict = {
        "proposal_id": "p1",
        "symbol": "INFY",
        "action": action,
        "side": OrderSide.SELL if action in (PortfolioAction.CLOSE_POSITION, PortfolioAction.REDUCE_POSITION) else OrderSide.BUY,
        "capital_allocation_inr": Decimal("20000"),
        "quantity_shares": 10,
        "confidence": 0.7,
        "thesis": "test exit",
    }
    fields.update(overrides)
    return TradeProposal(**fields)


def test_close_without_allocation_executes_at_stated_shares() -> None:
    service = ExecutionService(PaperBroker(Decimal("1000"), SystemClock(), {"INFY": Decimal("100")}))
    proposal = _proposal(PortfolioAction.CLOSE_POSITION, capital_allocation_inr=None)
    request = service.prepare("d1", "s1", proposal, Decimal("101"))
    assert request.quantity == 10
    assert request.side == OrderSide.SELL
    assert request.actual_order_value == Decimal("1010")
    assert request.requested_allocation == Decimal("1010")


def test_reduce_without_allocation_executes_partial() -> None:
    service = ExecutionService(PaperBroker(Decimal("1000"), SystemClock(), {"INFY": Decimal("100")}))
    proposal = _proposal(PortfolioAction.REDUCE_POSITION, capital_allocation_inr=None, quantity_shares=4)
    request = service.prepare("d1", "s1", proposal, Decimal("100"))
    assert request.quantity == 4
    assert request.actual_order_value == Decimal("400")


def test_open_without_allocation_still_refused() -> None:
    # The schema itself refuses allocation-free opens, so execution never sees one.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _proposal(PortfolioAction.OPEN_POSITION, capital_allocation_inr=None)


def test_close_without_shares_still_refused() -> None:
    service = ExecutionService(PaperBroker(Decimal("1000"), SystemClock(), {"INFY": Decimal("100")}))
    proposal = _proposal(PortfolioAction.CLOSE_POSITION, capital_allocation_inr=None, quantity_shares=None)
    with pytest.raises(ExecutionAdjustmentRequired):
        service.prepare("d1", "s1", proposal, Decimal("100"))
