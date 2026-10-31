"""Disabled live-order coordination.

This coordinator is never used by the paper-trading workflow. It exists so a future,
explicitly approved live path can submit an AI proposal exactly once, reconcile an
ambiguous broker response before any retry, and record the authoritative outcome.
"""

from dataclasses import dataclass

from ai_trader.brokers.live import LiveBroker, LiveBrokerError, LiveBrokerTimeout, LiveOrderUnknown
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.domain.decisions import TradeProposal
from ai_trader.domain.market import MarketTick
from ai_trader.domain.orders import Order, OrderRequest
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.execution.service import ExecutionAdjustmentRequired, ExecutionService
from ai_trader.safety.live import LiveSafetyEnvelope


@dataclass(frozen=True)
class LiveOrderOutcome:
    request_id: str
    status: str
    order: Order | None = None
    reconciled: bool = False
    error: str | None = None


class LiveOrderCoordinator:
    """Submit one live order per AI decision, then reconcile it authoritatively."""

    def __init__(
        self,
        broker: LiveBroker,
        safety: LiveSafetyEnvelope,
        execution: ExecutionService,
        clock: Clock | None = None,
    ) -> None:
        self.broker = broker
        self.safety = safety
        self.execution = execution
        self.clock = clock or SystemClock()

    async def submit_decision_order(
        self,
        *,
        decision_id: str,
        correlation_id: str,
        proposal: TradeProposal,
        tick: MarketTick,
        portfolio: PortfolioState,
    ) -> LiveOrderOutcome:
        try:
            request = self.execution.prepare(decision_id, correlation_id, proposal, tick.last_price)
        except ExecutionAdjustmentRequired as exc:
            return LiveOrderOutcome(
                request_id=decision_id, status="FAILED", error=f"execution: {exc}"
            )

        safety = self.safety.check(
            request, portfolio, broker_name=self.broker.broker_name, now=self.clock.now()
        )
        if not safety.allowed:
            return LiveOrderOutcome(
                request_id=decision_id, status="DENIED", error=safety.reason or "safety denied"
            )

        try:
            existing = await self.broker.reconcile_client_order(request.client_order_id)
            if existing is not None:
                order = await self.broker.reconcile_order(existing.broker_order_id)
                self.safety.record_submission()
                return LiveOrderOutcome(
                    request_id=decision_id, status="RECONCILED", order=order, reconciled=True
                )
            order = (await self.execution.submit(request)).order
            order = await self.broker.reconcile_order(order.broker_order_id)
            self.safety.record_submission()
            return LiveOrderOutcome(
                request_id=decision_id, status="SUBMITTED", order=order, reconciled=True
            )
        except LiveBrokerTimeout as exc:
            return await self._recover_unknown(request, error=f"timeout: {type(exc).__name__}")
        except LiveOrderUnknown as exc:
            return LiveOrderOutcome(request_id=decision_id, status="UNKNOWN", error=str(exc))
        except LiveBrokerError as exc:
            return LiveOrderOutcome(request_id=decision_id, status="FAILED", error=str(exc))

    async def _recover_unknown(self, request: OrderRequest, error: str) -> LiveOrderOutcome:
        """Recover from an ambiguous submission without blindly placing a second order."""
        try:
            existing = await self.broker.reconcile_client_order(request.client_order_id)
        except LiveBrokerError as exc:
            return LiveOrderOutcome(
                request_id=request.decision_id, status="UNKNOWN", error=str(exc)
            )
        if existing is None:
            return LiveOrderOutcome(request_id=request.decision_id, status="UNKNOWN", error=error)
        try:
            order = await self.broker.reconcile_order(existing.broker_order_id)
        except LiveBrokerError as exc:
            return LiveOrderOutcome(
                request_id=request.decision_id, status="UNKNOWN", error=str(exc)
            )
        self.safety.record_submission()
        return LiveOrderOutcome(
            request_id=request.decision_id, status="RECONCILED", order=order, reconciled=True
        )
