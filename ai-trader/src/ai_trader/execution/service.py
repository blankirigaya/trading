from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from ai_trader.brokers.base import Broker
from ai_trader.domain.decisions import PortfolioAction, TradeProposal
from ai_trader.domain.orders import Order, OrderRequest


class ExecutionAdjustmentRequired(ValueError):
    pass


# Live prices move between decision and execution. A stated share count whose
# live cost overshoots its allocation by more than this fraction is rejected
# instead of silently resized.
ALLOCATION_OVERSHOOT_FRACTION = Decimal("0.02")


@dataclass(frozen=True)
class ExecutionResult:
    order: Order
    request: OrderRequest


class ExecutionService:
    def __init__(
        self,
        broker: Broker,
        material_rounding_fraction: Decimal = Decimal("0.05"),
    ) -> None:
        self.broker = broker
        self.material_rounding_fraction = material_rounding_fraction
        self._submitted_decisions: dict[str, ExecutionResult] = {}

    def prepare(
        self, decision_id: str, correlation_id: str, proposal: TradeProposal, price: Decimal
    ) -> OrderRequest:
        """Perform only mechanical conversion; no broker side effects occur here."""
        if decision_id in self._submitted_decisions:
            return self._submitted_decisions[decision_id].request
        if proposal.symbol is None or proposal.side is None:
            raise ExecutionAdjustmentRequired("proposal does not describe an executable order")
        allocation = proposal.capital_allocation_inr
        if allocation is None:
            # Exits free capital instead of requiring it: a CLOSE/REDUCE names
            # shares, not a budget. Derive the notional from live price so the
            # request stays exact; never invent an allocation cap for an exit.
            if proposal.action not in (
                PortfolioAction.CLOSE_POSITION,
                PortfolioAction.REDUCE_POSITION,
            ):
                raise ExecutionAdjustmentRequired(
                    "proposal does not describe an executable order"
                )
            quantity = proposal.quantity_shares
            if quantity is None:
                # A full close without a share count means "close the position".
                # Defaulting to the held quantity keeps a requested exit
                # executable instead of silently dropping it.
                if proposal.action is not PortfolioAction.CLOSE_POSITION:
                    raise ExecutionAdjustmentRequired(
                        "proposal does not describe an executable order"
                    )
                position = getattr(self.broker, "positions", {}).get(proposal.symbol)
                if position is None or position.quantity <= 0:
                    raise ExecutionAdjustmentRequired(
                        "proposal does not describe an executable order"
                    )
                quantity = position.quantity
            allocation = price * quantity
        if proposal.quantity_shares is not None:
            # Stated shares are binding: the model names the exact share count
            # and the allocation acts as the budget cap.
            quantity = proposal.quantity_shares
            actual = price * quantity
            ceiling = allocation * (1 + ALLOCATION_OVERSHOOT_FRACTION)
            if actual > ceiling:
                raise ExecutionAdjustmentRequired(
                    f"stated quantity {quantity} shares costs {actual:.2f} at the live "
                    f"price, above the {allocation:.2f} allocation"
                )
        else:
            quantity = int(
                (allocation / price).to_integral_value(rounding=ROUND_DOWN)
            )
            if quantity < 1:
                raise ExecutionAdjustmentRequired("allocation cannot purchase one whole share")
            actual = price * quantity
            difference = allocation - actual
            if difference / allocation > self.material_rounding_fraction:
                raise ExecutionAdjustmentRequired(
                    "integer quantity would materially change requested allocation"
                )
        return OrderRequest(
            client_order_id=f"{decision_id}:{proposal.proposal_id}",
            decision_id=decision_id,
            correlation_id=correlation_id,
            symbol=proposal.symbol,
            exchange=proposal.exchange,
            segment=proposal.segment,
            side=proposal.side,
            order_type=proposal.order_type,
            quantity=quantity,
            limit_price=proposal.desired_entry_price
            if proposal.order_type.value == "LIMIT"
            else None,
            requested_allocation=allocation,
            actual_order_value=actual,
            conversion_reason="Whole NSE EQ share quantity rounded down from requested allocation.",
        )

    async def submit(self, request: OrderRequest) -> ExecutionResult:
        existing = self._submitted_decisions.get(request.decision_id)
        if existing is not None:
            return existing
        result = ExecutionResult(order=await self.broker.place_order(request), request=request)
        self._submitted_decisions[request.decision_id] = result
        return result

    async def execute(
        self, decision_id: str, correlation_id: str, proposal: TradeProposal, price: Decimal
    ) -> ExecutionResult:
        return await self.submit(self.prepare(decision_id, correlation_id, proposal, price))
