import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime

from ai_trader.ai.jev import JevCritic, JevUnavailable
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.ai.remote_model import RemoteAIUnavailable
from ai_trader.ai.retries import StructuredCompletionFailed
from ai_trader.brokers.paper import PaperBroker
from ai_trader.domain.common import Clock
from ai_trader.domain.decisions import (
    DecisionStatus,
    FinalAIDecision,
    PortfolioDecision,
)
from ai_trader.domain.market import MarketState
from ai_trader.domain.orders import Order, OrderRequest
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.execution.service import ExecutionAdjustmentRequired, ExecutionService
from ai_trader.journal.service import DecisionJournal, DecisionRecord
from ai_trader.news.review_context import NewsReviewBundle
from ai_trader.safety.envelope import SafetyEnvelope


class ShadowReviewWorkflow:
    """Fail-closed real-data workflow. Its concrete broker type is intentionally PaperBroker."""

    def __init__(
        self,
        manager: PortfolioManagerAgent,
        critic: JevCritic,
        broker: PaperBroker,
        safety: SafetyEnvelope,
        execution: ExecutionService,
        journal: DecisionJournal,
        clock: Clock,
        event_sink: Callable[..., None] | None = None,
        latency_sink: Callable[[str, str, datetime], None] | None = None,
    ) -> None:
        self.manager = manager
        self.critic = critic
        self.broker = broker
        self.safety = safety
        self.execution = execution
        self.journal = journal
        self.clock = clock
        self.event_sink = event_sink
        # (stage, analysis_id, timestamp) so news latency can be measured end to end.
        self.latency_sink = latency_sink

    def _emit(self, category: str, kind: str, message: str, *, symbol: str | None = None) -> None:
        if self.event_sink is not None:
            self.event_sink(category, kind, message, symbol=symbol)

    def _mark(self, stage: str, analysis_ids: tuple[str, ...]) -> None:
        """Record a latency stage for every analysis that triggered this review."""
        if self.latency_sink is None:
            return
        now = self.clock.now()
        for analysis_id in analysis_ids:
            self.latency_sink(stage, analysis_id, now)

    async def run(self, state: MarketState, news: NewsReviewBundle | None = None) -> DecisionRecord:
        event_id = state.snapshot_id
        decision_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:shadow-decision"))
        trigger_ids = news.trigger_item_ids if news is not None else ()
        before = await self.broker.get_account()
        context_hash = hashlib.sha256(
            (
                state.model_dump_json()
                + before.model_dump_json()
                + (news.as_prompt() if news else "")
            ).encode()
        ).hexdigest()
        if news is not None and not news.is_empty:
            self._emit(
                "SYSTEM",
                "news_review_started",
                f"Portfolio review started for {state.instrument.symbol}"
                f" with {len(news.source_items)} news item(s)",
                symbol=state.instrument.symbol,
            )
        self._emit(
            "SYSTEM", "ai_review_started", f"AI review started for {state.instrument.symbol}"
        )
        try:
            initial, metadata = await self.manager.review(state.recent_ticks[-1], before, news)
        except (RemoteAIUnavailable, StructuredCompletionFailed) as exc:
            self._emit("ERROR", "provider_error", f"Remote AI unavailable: {exc}")
            return await self._failure(decision_id, state, before, context_hash, f"local_ai: {exc}")
        self._emit(
            "AI",
            "ai_initial_decision",
            f"Initial AI decision: {self._describe(initial)}",
        )
        record = DecisionRecord(
            decision_id=decision_id,
            correlation_id=state.snapshot_id,
            event_id=event_id,
            timestamp=self.clock.now(),
            status=DecisionStatus.CREATED,
            input_context_hash=context_hash,
            initial_ai_proposal=initial,
            initial_provider_metadata=metadata,
            portfolio_before=before,
            market_snapshot_id=state.snapshot_id,
            market_timestamp=state.timestamp,
            instrument_key=state.instrument.instrument_key,
            symbol=state.instrument.symbol,
        )
        try:
            critique, critique_metadata = await self.critic.assess(initial, news)
        except JevUnavailable as exc:
            self._emit("ERROR", "provider_error", f"Jev unavailable: {exc}")
            record = record.model_copy(
                update={"status": DecisionStatus.FAILED, "failure_reason": f"jev: {exc}"}
            )
            await self.journal.save(record)
            return record
        self._emit(
            "JEV",
            "jev_critique",
            f"Jev critique: {critique.bounded_decisions[0] if critique.bounded_decisions else 'APPROVE'}",
        )
        final_proposal = initial
        status = DecisionStatus.APPROVED_BY_AI
        reconsideration_requested = not critique.agrees or (
            "REQUEST_RECONSIDERATION" in critique.bounded_decisions
        )
        reconsideration_performed = False
        if reconsideration_requested:
            try:
                final_proposal, _ = await self.manager.reconsider(
                    state.recent_ticks[-1], before, critique.critique, news
                )
                status = DecisionStatus.RECONSIDERED
                reconsideration_performed = True
                self._emit("AI", "ai_reconsideration", "AI reconsidered after Jev challenge")
            except (RemoteAIUnavailable, StructuredCompletionFailed) as exc:
                self._emit("ERROR", "provider_error", f"Reconsideration failed: {exc}")
                record = record.model_copy(
                    update={
                        "status": DecisionStatus.FAILED,
                        "failure_reason": f"reconsideration: {exc}",
                        "reconsideration_requested": True,
                    }
                )
                await self.journal.save(record)
                return record
        final = FinalAIDecision(
            decision_id=decision_id,
            correlation_id=state.snapshot_id,
            approved=True,
            decision_summary=final_proposal.decision_summary,
            proposals=final_proposal.proposals,
        )
        record = record.model_copy(
            update={
                "status": status,
                "jev_critique": critique,
                "jev_provider_metadata": critique_metadata,
                "final_ai_decision": final,
                "reconsideration_requested": reconsideration_requested,
                "reconsideration_performed": reconsideration_performed,
            }
        )
        self._emit(
            "AI", "ai_final_decision", f"Final AI decision: {self._describe(final_proposal)}"
        )
        # The AI has now judged, with news in hand. This is the news-to-decision point.
        self._mark("decision", trigger_ids)
        if not final.proposals:
            self._emit("AI", "ai_hold", "AI chose to hold or stay in cash")
            await self.journal.save(record)
            return record
        try:
            request = self.execution.prepare(
                decision_id, state.snapshot_id, final.proposals[0], state.last_price
            )
        except ExecutionAdjustmentRequired as exc:
            record = record.model_copy(
                update={"status": DecisionStatus.FAILED, "failure_reason": f"execution: {exc}"}
            )
            await self.journal.save(record)
            return record
        result = self.safety.check(request, before)
        self._emit(
            "SAFETY",
            "safety_result",
            f"Safety {'PASS' if result.allowed else 'DENY'}"
            + (f": {result.reason}" if not result.allowed and result.reason else ""),
        )
        if not result.allowed:
            record = record.model_copy(
                update={
                    "status": DecisionStatus.DENIED_BY_SAFETY,
                    "failure_reason": result.reason or "safety denied",
                    "safety_allowed": False,
                    "safety_reason": result.reason,
                }
            )
            await self.journal.save(record)
            return record
        order, record = await self._submit(request, record)
        if order is None:
            return record
        self.safety.record_submission()
        self._emit(
            "PAPER",
            "paper_order",
            f"{request.side.value} {request.quantity} {request.symbol} @ {order.request.actual_order_value} INR (paper)",
        )
        self._emit(
            "PAPER", "paper_fill", f"Paper fill {order.broker_order_id} {order.status.value}"
        )
        record = record.model_copy(
            update={
                "status": DecisionStatus.EXECUTED,
                "orders_generated": [order],
                "portfolio_after": await self.broker.get_account(),
                "safety_allowed": True,
                "safety_reason": None,
            }
        )
        await self.journal.save(record)
        self._emit("JOURNAL", "decision_persisted", f"Decision {decision_id} persisted")
        # End of the news-to-trade chain: the paper order exists.
        self._mark("paper_order", trigger_ids)
        return record

    def _describe(self, decision: PortfolioDecision) -> str:
        """Short human-readable summary of a structured decision for the timeline."""
        if not decision.proposals:
            return f"hold (cash {decision.cash_percent:.0f}%)"
        parts = []
        for proposal in decision.proposals:
            side = proposal.side.value if proposal.side else "?"
            allocation = (
                f"{proposal.capital_allocation_inr:.0f} INR"
                if proposal.capital_allocation_inr
                else "n/a"
            )
            parts.append(f"{side} {proposal.symbol} {allocation} conf {proposal.confidence:.0%}")
        return "; ".join(parts)

    async def _submit(
        self, request: OrderRequest, record: DecisionRecord
    ) -> tuple[Order | None, DecisionRecord]:
        """Submit to the paper broker, journalling mechanical rejections.

        A broker-level refusal (for example selling a position that is not held) is a
        mechanical outcome, not a crash: it fails the decision closed with a reason.
        """
        try:
            return (await self.execution.submit(request)).order, record
        except (ValueError, ExecutionAdjustmentRequired) as exc:
            failed = record.model_copy(
                update={
                    "status": DecisionStatus.FAILED,
                    "failure_reason": f"paper_broker: {exc}",
                }
            )
            await self.journal.save(failed)
            return None, failed

    async def _failure(
        self,
        decision_id: str,
        state: MarketState,
        before: PortfolioState,
        context_hash: str,
        reason: str,
    ) -> DecisionRecord:
        from ai_trader.domain.decisions import ProviderMetadata

        record = DecisionRecord(
            decision_id=decision_id,
            correlation_id=state.snapshot_id,
            event_id=state.snapshot_id,
            timestamp=self.clock.now(),
            status=DecisionStatus.FAILED,
            input_context_hash=context_hash,
            initial_ai_proposal=PortfolioDecision(
                cash_percent=100,
                allocations=[],
                proposals=[],
                market_summary="Remote AI unavailable",
                decision_summary="No new trade: remote AI service did not return a valid decision.",
            ),
            initial_provider_metadata=ProviderMetadata(
                provider="remote_openai_compatible",
                model=self.manager.provider.model_name,
                prompt_name="portfolio_manager",
                prompt_version=self.manager.prompt_version,
            ),
            portfolio_before=before,
            failure_reason=reason,
            market_snapshot_id=state.snapshot_id,
            market_timestamp=state.timestamp,
            instrument_key=state.instrument.instrument_key,
            symbol=state.instrument.symbol,
        )
        await self.journal.save(record)
        return record
