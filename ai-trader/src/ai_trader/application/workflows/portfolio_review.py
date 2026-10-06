import hashlib
import uuid

from ai_trader.ai.jev import JevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.ai.scouts import MarketScout, ScoutPriority
from ai_trader.brokers.base import Broker
from ai_trader.domain.common import Clock
from ai_trader.domain.decisions import DecisionStatus, FinalAIDecision
from ai_trader.domain.events import (
    AIProposalCreated,
    FinalAIDecisionCreated,
    MarketTickReceived,
    OrderFilled,
    OrderSubmitted,
    PortfolioReviewRequested,
    SafetyViolation,
)
from ai_trader.events.bus import EventBus
from ai_trader.execution.service import ExecutionAdjustmentRequired, ExecutionService
from ai_trader.journal.service import DecisionJournal, DecisionRecord
from ai_trader.safety.envelope import SafetyEnvelope


class PortfolioReviewWorkflow:
    def __init__(
        self,
        *,
        bus: EventBus,
        scout: MarketScout,
        manager: PortfolioManagerAgent,
        critic: JevCritic,
        broker: Broker,
        safety: SafetyEnvelope,
        execution: ExecutionService,
        journal: DecisionJournal,
        clock: Clock,
    ) -> None:
        self.bus = bus
        self.scout = scout
        self.manager = manager
        self.critic = critic
        self.broker = broker
        self.safety = safety
        self.execution = execution
        self.journal = journal
        self.clock = clock

    async def handle_market_tick(self, event: MarketTickReceived) -> None:
        scout_result = await self.scout.evaluate(event.tick)
        if scout_result.priority == ScoutPriority.IGNORE:
            return
        await self.run(event)

    async def run(self, event: MarketTickReceived) -> DecisionRecord:
        await self.bus.publish(
            PortfolioReviewRequested(
                event_id=self._id(event.event_id, "review"),
                correlation_id=event.correlation_id,
                occurred_at=self.clock.now(),
                source_event_id=event.event_id,
                tick=event.tick,
            )
        )
        portfolio_before = await self.broker.get_account()
        proposal, proposal_metadata = await self.manager.review(event.tick, portfolio_before)
        decision_id = self._id(event.event_id, "decision")
        first_proposal = proposal.proposals[0] if proposal.proposals else None
        if first_proposal:
            await self.bus.publish(
                AIProposalCreated(
                    event_id=self._id(event.event_id, "proposal"),
                    correlation_id=event.correlation_id,
                    occurred_at=self.clock.now(),
                    proposal=first_proposal,
                )
            )
        context_hash = hashlib.sha256(
            (event.tick.model_dump_json() + portfolio_before.model_dump_json()).encode()
        ).hexdigest()
        record = DecisionRecord(
            decision_id=decision_id,
            correlation_id=event.correlation_id,
            event_id=event.event_id,
            timestamp=self.clock.now(),
            status=DecisionStatus.CREATED,
            input_context_hash=context_hash,
            initial_ai_proposal=proposal,
            initial_provider_metadata=proposal_metadata,
            portfolio_before=portfolio_before,
        )
        await self.journal.save(record)
        critique, critique_metadata = await self.critic.assess(proposal)
        final = FinalAIDecision(
            decision_id=decision_id,
            correlation_id=event.correlation_id,
            approved=critique.agrees,
            decision_summary="Mock final decision accepts the initial structured proposal."
            if critique.agrees
            else "Critic denied proposal.",
            proposals=proposal.proposals if critique.agrees else [],
        )
        record = record.model_copy(
            update={
                "status": DecisionStatus.APPROVED_BY_AI
                if final.approved
                else DecisionStatus.CRITIQUED,
                "jev_critique": critique,
                "jev_provider_metadata": critique_metadata,
                "final_ai_decision": final,
            }
        )
        await self.bus.publish(
            FinalAIDecisionCreated(
                event_id=self._id(event.event_id, "final"),
                correlation_id=event.correlation_id,
                occurred_at=self.clock.now(),
                decision=final,
            )
        )
        if not final.approved or not final.proposals:
            await self.journal.save(record)
            return record
        proposal_to_execute = final.proposals[0]
        try:
            request = self.execution.prepare(
                decision_id, event.correlation_id, proposal_to_execute, event.tick.last_price
            )
        except ExecutionAdjustmentRequired:
            record = record.model_copy(update={"status": DecisionStatus.FAILED})
            await self.journal.save(record)
            return record
        safety = self.safety.check(request, portfolio_before)
        if not safety.allowed:
            record = record.model_copy(update={"status": DecisionStatus.DENIED_BY_SAFETY})
            await self.bus.publish(
                SafetyViolation(
                    event_id=self._id(event.event_id, "safety"),
                    correlation_id=event.correlation_id,
                    occurred_at=self.clock.now(),
                    decision_id=decision_id,
                    reason=safety.reason or "Denied",
                )
            )
            await self.journal.save(record)
            return record
        execution_result = await self.execution.submit(request)
        order = execution_result.order
        self.safety.record_submission()
        record = record.model_copy(
            update={
                "status": DecisionStatus.EXECUTED,
                "orders_generated": [order],
                "portfolio_after": await self.broker.get_account(),
            }
        )
        await self.bus.publish(
            OrderSubmitted(
                event_id=self._id(event.event_id, "submitted"),
                correlation_id=event.correlation_id,
                occurred_at=self.clock.now(),
                order=order,
            )
        )
        fill = next(
            fill
            for fill in getattr(self.broker, "fills", [])
            if fill.broker_order_id == order.broker_order_id
        )
        await self.bus.publish(
            OrderFilled(
                event_id=self._id(event.event_id, "filled"),
                correlation_id=event.correlation_id,
                occurred_at=self.clock.now(),
                fill=fill,
            )
        )
        await self.journal.save(record)
        return record

    @staticmethod
    def _id(event_id: str, suffix: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:{suffix}"))
