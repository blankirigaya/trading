import hashlib
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from ai_trader.ai.jev import JevCritic, JevUnavailable
from ai_trader.ai.portfolio_manager import (
    OpenPositionBrief,
    PortfolioManagerAgent,
    render_open_position_brief,
)
from ai_trader.ai.remote_model import RemoteAIUnavailable
from ai_trader.ai.retries import StructuredCompletionFailed
from ai_trader.brokers.charges import ChargeRates, evaluate_entry_edge
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
from ai_trader.domain.orders import Order, OrderRequest, OrderSide
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
ENTRY_ACTIONS = frozenset({PortfolioAction.OPEN_POSITION, PortfolioAction.INCREASE_POSITION})


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
        jev_min_entry_score: float = 0.65,
        charge_rates: ChargeRates | None = None,
        min_edge_cost_multiple: Decimal = Decimal("2.5"),
    ) -> None:
        self.manager = manager
        self.critic = critic
        self.broker = broker
        self.safety = safety
        self.execution = execution
        self.journal = journal
        self.jev_min_entry_score = jev_min_entry_score
        self.charge_rates = charge_rates
        self.min_edge_cost_multiple = min_edge_cost_multiple
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

    async def _open_position_brief(
        self, portfolio: PortfolioState, prices: Mapping[str, Decimal] | None
    ) -> str:
        """Pre-digested exit block: unrealized P&L, age, and the entry plan.

        Best effort by design: any lookup failure degrades that position's
        line to the raw book facts instead of failing the review. Entry plans
        come from the journaled entry decision the ledger links to the open
        position.
        """
        return render_open_position_brief(await self._position_views(portfolio, prices))

    async def _position_views(
        self, portfolio: PortfolioState, prices: Mapping[str, Decimal] | None
    ) -> list[OpenPositionBrief]:
        views: list[OpenPositionBrief] = []
        marks = prices or {}
        for position in portfolio.positions:
            if not position.quantity:
                continue
            symbol = position.symbol
            ltp = marks.get(symbol, marks.get(symbol.upper()))
            unrealized: Decimal | None = None
            unrealized_pct: Decimal | None = None
            if ltp is not None and ltp > 0:
                unrealized = (ltp - position.average_entry_price) * position.quantity
                notional = position.average_entry_price * position.quantity
                if notional > 0:
                    unrealized_pct = unrealized / notional * Decimal(100)
            held_minutes: int | None = None
            target: Decimal | None = None
            invalidation: Decimal | None = None
            thesis: str | None = None
            try:
                ledger = getattr(self.broker, "ledger", None)
                shard = getattr(self.broker, "shard", 0)
                entry_id: str | None = None
                opened_at = None
                if ledger is not None:
                    entry_id, opened_at = await ledger.position_open_context(
                        shard, symbol
                    )
                if opened_at is not None:
                    held_minutes = int(
                        (self.clock.now() - opened_at).total_seconds() // 60
                    )
                if entry_id:
                    record = await self.journal.get(entry_id)
                    proposals = (
                        record.initial_ai_proposal.proposals if record is not None else []
                    )
                    entry = next(
                        (
                            proposal
                            for proposal in proposals
                            if (proposal.symbol or "").upper() == symbol.upper()
                        ),
                        None,
                    )
                    if entry is not None:
                        target = entry.target_price
                        invalidation = entry.invalidation_price
                        thesis = entry.thesis
            except Exception:  # noqa: BLE001 - brief enrichment must never fail a review
                logger.debug(
                    "position brief enrichment unavailable for %s", symbol, exc_info=True
                )
            views.append(
                OpenPositionBrief(
                    symbol=symbol,
                    quantity=position.quantity,
                    average_entry_price=position.average_entry_price,
                    last_price=ltp,
                    unrealized_inr=unrealized,
                    unrealized_pct=unrealized_pct,
                    held_minutes=held_minutes,
                    target_price=target,
                    invalidation_price=invalidation,
                    entry_thesis=thesis,
                )
            )
        return views

    async def _enforce_invalidations(
        self, views: list[OpenPositionBrief], snapshot_id: str
    ) -> None:
        """Close positions trading at or below their AI-set invalidation level.

        Runs before any AI judgment so a failed or indecisive review cannot
        strand a broken trade. No invalidation recorded, no live mark, or an
        engaged kill switch all mean hands off: never invent an exit level and
        never trade blind. Closes go straight to the paper broker (exits only
        ever reduce exposure, so no safety check); failures are logged and the
        review continues so one stuck symbol cannot block the board.
        """
        if getattr(self.safety.kill_switch, "enabled", False):
            return
        for view in views:
            if view.invalidation_price is None:
                continue
            if view.last_price is None or view.last_price <= 0:
                continue
            if view.last_price > view.invalidation_price:
                continue
            proposal = TradeProposal(
                proposal_id=f"guardrail-{view.symbol}-{uuid.uuid4().hex[:8]}",
                symbol=view.symbol,
                exchange="NSE",
                action=PortfolioAction.CLOSE_POSITION,
                side=OrderSide.SELL,
                capital_allocation_inr=None,
                quantity_shares=view.quantity,
                confidence=1.0,
                thesis=(
                    f"Invalidation guardrail: {view.symbol} at {view.last_price:.2f} "
                    f"reached the entry plan's invalidation level {view.invalidation_price:.2f}."
                ),
                evidence=[f"LTP {view.last_price:.2f} <= invalidation {view.invalidation_price:.2f}"],
                conditions_that_change_thesis=[],
            )
            try:
                request = self.execution.prepare(
                    f"guardrail-{uuid.uuid4().hex[:12]}",
                    snapshot_id,
                    proposal,
                    view.last_price,
                )
                await self.execution.submit(request)
            except Exception as exc:  # noqa: BLE001 - one stuck exit must not stop reviews
                logger.warning("invalidation guardrail failed for %s: %s", view.symbol, exc)
                continue
            self._emit(
                "PAPER",
                "guardrail_close",
                f"INVALIDATION {view.symbol} {view.quantity} @ {view.last_price:.2f} "
                f"(invalidation {view.invalidation_price:.2f}, paper)",
                symbol=view.symbol,
            )
            self.safety.record_submission()

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
        views = await self._position_views(before, prices)
        position_brief = render_open_position_brief(views)
        # Invalidation guardrail: the AI's own thesis-break level is enforced
        # deterministically before any AI judgment, so a failed AI review can
        # never strand a broken trade. Exits only; entries are the Jev gate's job.
        await self._enforce_invalidations(views, state.snapshot_id)
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
                state.recent_ticks[-1], before, news, board=board_states,
                position_brief=position_brief,
            )
        except (RemoteAIUnavailable, StructuredCompletionFailed) as exc:
            self._emit("ERROR", "provider_error", f"Remote AI unavailable: {exc}")
            return await self._failure(decision_id, state, before, context_hash, f"ai: {exc}")
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
        # Jev entry bar: entries need a convincingly scored thesis; weak agrees
        # used to sail through and bleed on charges. Exits are never gated —
        # banking a winner must stay frictionless.
        entry_proposals = [
            proposal
            for proposal in final_proposal.proposals
            if proposal.action in ENTRY_ACTIONS
        ]
        exit_proposals = [
            proposal
            for proposal in final_proposal.proposals
            if proposal.action in EXIT_ACTIONS
        ]
        if (
            entry_proposals
            and not exit_proposals
            and getattr(self.critic, "assesses", True)
            and critique.score < self.jev_min_entry_score
        ):
            reason = (
                f"jev gate: entry score {critique.score:.2f} below minimum "
                f"{self.jev_min_entry_score:.2f}"
            )
            self._emit("SAFETY", "safety_result", f"DENY: {reason}")
            record = record.model_copy(
                update={
                    "status": DecisionStatus.DENIED_BY_SAFETY,
                    "failure_reason": reason,
                    "safety_allowed": False,
                    "safety_reason": reason,
                }
            )
            await self.journal.save(record)
            return record
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
        edge_reason = self._entry_edge_reason(executable[0], exec_state.last_price)
        if edge_reason is not None:
            # Deterministic last line before the order is built: a target that
            # cannot pay for its own two fills is refused regardless of how
            # confident the model or the critic is.
            self._emit("SAFETY", "safety_result", f"DENY: {edge_reason}")
            record = record.model_copy(
                update={
                    "status": DecisionStatus.DENIED_BY_SAFETY,
                    "failure_reason": edge_reason,
                    "safety_allowed": False,
                    "safety_reason": edge_reason,
                }
            )
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

    def _entry_edge_reason(self, proposal: TradeProposal, price: Decimal) -> str | None:
        """Refuse an entry whose stated target cannot pay for its own round trip.

        Purely arithmetic over the charge schedule — no AI call, no judgement,
        so it cannot be argued out of by a confident model. Applies only to
        entries: exits must stay frictionless even when they realise a loss.
        A proposal with no target (or no way to size it) is not refused here;
        it is simply unverifiable and keeps the existing execution checks.
        """
        if proposal.action not in ENTRY_ACTIONS:
            return None
        if self.charge_rates is None or self.min_edge_cost_multiple <= 0:
            return None
        entry_price = proposal.desired_entry_price or price
        quantity = proposal.quantity_shares
        if quantity is None:
            allocation = proposal.capital_allocation_inr
            if allocation is None or entry_price <= 0:
                return None
            quantity = int((allocation / entry_price).to_integral_value(rounding=ROUND_DOWN))
        edge = evaluate_entry_edge(
            entry_price=entry_price,
            target_price=proposal.target_price,
            quantity=quantity,
            rates=self.charge_rates,
            required_multiple=self.min_edge_cost_multiple,
        )
        if edge.multiple is None or edge.allowed:
            return None
        return (
            f"edge gate: target {proposal.target_price} earns "
            f"{edge.expected_gross_inr:.2f} INR gross against "
            f"{edge.round_trip_cost_inr:.2f} INR round-trip charges "
            f"({edge.multiple:.2f}x, needs {edge.required_multiple}x)"
        )

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
                decision_summary=(
                    "No decision produced: the AI provider returned no usable decision."
                ),
            ),
            initial_provider_metadata=ProviderMetadata(
                provider=self.manager.provider.provider_label,
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
