import hashlib
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal

from ai_trader.ai.jev import JevCritic, JevUnavailable
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.ai.remote_model import RemoteAIUnavailable
from ai_trader.ai.retries import StructuredCompletionFailed
from ai_trader.brokers.paper import PaperBroker
from ai_trader.domain.common import Clock
from ai_trader.domain.decisions import (
    DecisionStatus,
    FinalAIDecision,
    PortfolioAction,
    PortfolioDecision,
    ProviderMetadata,
    TradeProposal,
)
from ai_trader.domain.market import Instrument, MarketState
from ai_trader.domain.orders import Order, OrderRequest
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.execution.service import ExecutionAdjustmentRequired, ExecutionService
from ai_trader.journal.service import DecisionJournal, DecisionRecord
from ai_trader.news.review_context import NewsReviewBundle
from ai_trader.safety.envelope import SafetyEnvelope

logger = logging.getLogger(__name__)

TRADE_ACTIONS = frozenset(
    {
        PortfolioAction.OPEN_POSITION,
        PortfolioAction.INCREASE_POSITION,
        PortfolioAction.REDUCE_POSITION,
        PortfolioAction.CLOSE_POSITION,
    }
)
EXIT_ACTIONS = frozenset({PortfolioAction.CLOSE_POSITION, PortfolioAction.REDUCE_POSITION})


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

    @staticmethod
    def _state_for_symbol(
        proposals: list[TradeProposal],
        primary: MarketState,
        board: list[MarketState],
    ) -> MarketState:
        """Resolve the market state a proposal actually trades on.

        The AI picks its own symbol from the board; execution, valuation, and
        the journal must use that symbol's live price, never the primary
        state's. Unknown symbols fall back to the primary state and fail
        closed downstream.
        """
        for proposal in proposals:
            if not proposal.symbol:
                continue
            wanted = proposal.symbol.upper()
            for state in board:
                if state.instrument.symbol.upper() == wanted:
                    return state
        return primary

    @staticmethod
    def _mark_state(
        state: MarketState, proposal: TradeProposal, price: Decimal | None
    ) -> MarketState:
        """A mark-only snapshot for a held symbol that is missing from the board.

        Exits must not wait for a fresh board: if a held symbol's feed goes
        stale the position still has to be closable. The instrument, price, and
        snapshot id come from the session's live mark; everything else stays the
        reviewed snapshot so the journal still points at the decision's context.
        """
        if price is None or price <= 0:
            return state
        symbol = (proposal.symbol or "").upper()
        return state.model_copy(
            update={
                "snapshot_id": f"{state.snapshot_id}:mark:{symbol}",
                "instrument": Instrument(
                    instrument_key=f"{proposal.exchange}:{symbol}-{proposal.segment}",
                    symbol=symbol,
                    exchange=proposal.exchange,
                    segment=proposal.segment,
                ),
                "last_price": price,
            }
        )

    @staticmethod
    def _drop_reason(
        proposal: TradeProposal,
        board_symbols: set[str],
        held_symbols: set[str],
        marks: Mapping[str, Decimal],
    ) -> str:
        """Why a proposal was not executable, so a dropped exit is diagnosable."""
        symbol = (proposal.symbol or "?").upper()
        reasons: list[str] = []
        if proposal.symbol is None:
            reasons.append("no symbol")
        if proposal.side is None:
            reasons.append("no side")
        if proposal.action not in TRADE_ACTIONS:
            reasons.append(f"{proposal.action.value} is not a trade action")
        if proposal.capital_allocation_inr is None and not (
            proposal.action in EXIT_ACTIONS and proposal.quantity_shares is not None
        ):
            reasons.append("no allocation and no exit share count")
        if proposal.symbol is not None:
            if proposal.action in EXIT_ACTIONS and symbol not in held_symbols:
                reasons.append("exit for a symbol that is not held")
            elif symbol in board_symbols:
                pass
            elif proposal.action in EXIT_ACTIONS and symbol in marks:
                pass
            elif proposal.action in EXIT_ACTIONS:
                reasons.append("no live mark price for the exit")
            else:
                reasons.append("symbol not on the reviewed board")
        return f"{proposal.action.value} {symbol}: {', '.join(reasons) or 'not executable'}"

    async def run(
        self,
        state: MarketState,
        news: NewsReviewBundle | None = None,
        *,
        board: list[MarketState] | None = None,
        prices: Mapping[str, Decimal] | None = None,
    ) -> DecisionRecord:
        board_states = list(board) if board else [state]
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
            initial, metadata = await self.manager.review(
                state.recent_ticks[-1], before, news, board=board_states
            )
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
            cause = exc.__cause__
            reason = f"jev: {exc}"
            if cause is not None:
                # Persist the transport root cause (e.g. ConnectError, ReadTimeout)
                # so a failed review is diagnosable instead of a mystery. HTTP
                # errors never carry credentials (the key travels in headers,
                # never in the URL), and the text is capped for journal tidiness.
                detail = f"{type(cause).__name__}: {cause}"
                reason += f" (caused by {detail[:280]})"
            self._emit("ERROR", "provider_error", f"Jev unavailable: {exc}")
            record = record.model_copy(
                update={"status": DecisionStatus.FAILED, "failure_reason": reason}
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
        reconsider_metadata: ProviderMetadata | None = None
        if reconsideration_requested:
            try:
                recon_state = self._state_for_symbol(initial.proposals, state, board_states)
                final_proposal, reconsider_metadata = await self.manager.reconsider(
                    recon_state.recent_ticks[-1], before, critique.critique, news
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
        board_symbols = {s.instrument.symbol.upper() for s in board_states}
        # Live mark per symbol from the session's last feed snapshot, including
        # symbols whose state is too stale to review.
        marks = {symbol.upper(): price for symbol, price in (prices or {}).items()}
        # A held position stays closable even when its symbol is missing from the
        # fresh board: an exit must never be stranded by a data gap.
        held_symbols = {
            position.symbol.upper() for position in before.positions if position.quantity > 0
        }
        executable = [
            proposal
            for proposal in final.proposals
            if proposal.action in TRADE_ACTIONS
            and proposal.symbol is not None
            and proposal.side is not None
            and (
                proposal.capital_allocation_inr is not None
                or (
                    proposal.action
                    in (PortfolioAction.CLOSE_POSITION, PortfolioAction.REDUCE_POSITION)
                    and proposal.quantity_shares is not None
                )
            )
            and (
                proposal.symbol.upper() in board_symbols
                or (
                    proposal.action in EXIT_ACTIONS
                    and proposal.symbol.upper() in held_symbols
                    and marks.get(proposal.symbol.upper(), Decimal("0")) > 0
                )
            )
        ]
        exec_state = self._state_for_symbol(executable[:1] or final.proposals, state, board_states)
        if executable and executable[0].symbol is not None:
            symbol = executable[0].symbol.upper()
            if symbol not in board_symbols:
                exec_state = self._mark_state(exec_state, executable[0], marks.get(symbol))
        record = record.model_copy(
            update={
                "status": status,
                "jev_critique": critique,
                "jev_provider_metadata": critique_metadata,
                "final_ai_decision": final,
                "reconsideration_requested": reconsideration_requested,
                "reconsideration_performed": reconsideration_performed,
                "reconsideration_provider_metadata": reconsider_metadata
                if reconsideration_performed
                else None,
                "market_snapshot_id": exec_state.snapshot_id,
                "market_timestamp": exec_state.timestamp,
                "instrument_key": exec_state.instrument.instrument_key,
                "symbol": exec_state.instrument.symbol,
            }
        )
        self._emit(
            "AI", "ai_final_decision", f"Final AI decision: {self._describe(final_proposal)}"
        )
        # The AI has now judged, with news in hand. This is the news-to-decision point.
        self._mark("decision", trigger_ids)
        if not executable:
            if final.proposals:
                reasons = "; ".join(
                    self._drop_reason(proposal, board_symbols, held_symbols, marks)
                    for proposal in final.proposals
                )
                if any(proposal.action in TRADE_ACTIONS for proposal in final.proposals):
                    logger.warning("dropped AI trade intent decision=%s: %s", decision_id, reasons)
                self._emit(
                    "AI",
                    "ai_hold",
                    "AI proposals name no reviewed or no tradable symbol; holding instead of"
                    f" trading blind ({reasons})",
                )
            else:
                self._emit("AI", "ai_hold", "AI chose to hold or stay in cash")
            await self.journal.save(record)
            return record
        try:
            request = self.execution.prepare(
                decision_id, exec_state.snapshot_id, executable[0], exec_state.last_price
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
        That applies to persistence too. A journal write that fails must not escape:
        the position has already moved, and in production an ``OperationalError``
        here escaped into the cadence loop and killed every later review.
        """
        try:
            return (await self.execution.submit(request)).order, record
        except (ValueError, ExecutionAdjustmentRequired) as exc:
            reason = f"paper_broker: {exc}"
        except Exception as exc:  # noqa: BLE001 - one bad order must not stop reviews
            logger.exception("paper submission failed for %s", request.symbol)
            self._emit("ERROR", "execution_error", f"Paper submission failed: {exc}")
            reason = f"execution_error: {exc}"
        failed = record.model_copy(
            update={
                "status": DecisionStatus.FAILED,
                "failure_reason": reason,
            }
        )
        try:
            await self.journal.save(failed)
        except Exception as exc:  # noqa: BLE001 - journal is best-effort here
            logger.exception("could not journal failed decision %s", record.decision_id)
            self._emit(
                "ERROR",
                "decision_not_persisted",
                f"Decision {record.decision_id} failed to persist: {exc}",
            )
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
