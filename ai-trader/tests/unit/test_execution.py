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


def _proposal_with_shares(amount: str, shares: int) -> TradeProposal:
    return proposal(amount).model_copy(update={"quantity_shares": shares})


def test_stated_shares_are_binding_at_live_price(clock) -> None:
    # 20 shares trade verbatim even though 2000 / 99 would derive a different count.
    service = ExecutionService(PaperBroker(Decimal("2000"), clock, {"T": Decimal("99")}))
    request = service.prepare("d", "c", _proposal_with_shares("2000", 20), Decimal("99"))
    assert request.quantity == 20
    assert request.actual_order_value == Decimal("1980")


def test_stated_shares_above_allocation_are_rejected(clock) -> None:
    # 20 shares at 99 = 1980, far above the 1000 allocation.
    service = ExecutionService(PaperBroker(Decimal("2000"), clock, {"T": Decimal("99")}))
    with pytest.raises(ExecutionAdjustmentRequired, match="above the"):
        service.prepare("d", "c", _proposal_with_shares("1000", 20), Decimal("99"))


def test_stated_shares_tolerate_small_price_drift(clock) -> None:
    # 10 shares at 99 = 990 against a 1000 allocation: within the 2% overshoot.
    service = ExecutionService(PaperBroker(Decimal("2000"), clock, {"T": Decimal("99")}))
    request = service.prepare("d", "c", _proposal_with_shares("1000", 10), Decimal("99"))
    assert request.quantity == 10


def test_missing_shares_fall_back_to_derived_quantity(clock) -> None:
    service = ExecutionService(PaperBroker(Decimal("2000"), clock, {"T": Decimal("99")}))
    request = service.prepare("d", "c", proposal("1000"), Decimal("99"))
    assert request.quantity == 10
