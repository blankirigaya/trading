from decimal import Decimal

import pytest

from ai_trader.brokers.paper import PaperBroker
from ai_trader.domain.decisions import PortfolioAction, TradeProposal
from ai_trader.domain.orders import OrderSide
from ai_trader.execution.service import ExecutionAdjustmentRequired, ExecutionService


def proposal(amount: str = "1000") -> TradeProposal:
    return TradeProposal(
        proposal_id="p",
        symbol="T",
        action=PortfolioAction.OPEN_POSITION,
        side=OrderSide.BUY,
        capital_allocation_inr=Decimal(amount),
        confidence=0.5,
        thesis="test",
        evidence=[],
        conditions_that_change_thesis=[],
    )


async def test_execution_rounds_whole_quantity_and_is_idempotent(clock) -> None:
    broker = PaperBroker(Decimal("2000"), clock, {"T": Decimal("99")})
    service = ExecutionService(broker)
    first = await service.execute("d", "c", proposal(), Decimal("99"))
    second = await service.execute("d", "c", proposal(), Decimal("99"))
    assert first.request.quantity == 10
    assert first.request.actual_order_value == Decimal("990")
    assert second.order.broker_order_id == first.order.broker_order_id
    assert len(await broker.get_orders()) == 1


def test_execution_rejects_material_rounding(clock) -> None:
    service = ExecutionService(PaperBroker(Decimal("1000"), clock, {"T": Decimal("90")}))
    with pytest.raises(ExecutionAdjustmentRequired):
        service.prepare("d", "c", proposal("100"), Decimal("90"))
