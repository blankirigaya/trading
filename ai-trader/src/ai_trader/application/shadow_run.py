"""One-shot live-shadow acceptance run.

Runs the real path exactly once:
real Upstox V3 state -> remote AI -> real Jev -> optional remote AI reconsideration
-> safety -> PaperBroker -> SQL journal.

It never substitutes mocks and never forces a trade. A HOLD or STAY_IN_CASH result is a
successful autonomous decision.
"""

from dataclasses import dataclass

from ai_trader.ai.jev import build_critic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent, position_limits_text
from ai_trader.ai.providers import build_ai_provider
from ai_trader.ai.retries import RetryingAIProvider
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.charges import charge_rates_for
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import SystemClock
from ai_trader.domain.decisions import TradeProposal
from ai_trader.domain.market import MarketState, stale_data_reason
from ai_trader.execution.service import ExecutionService
from ai_trader.integrations.connectivity import (
    probe_ai,
    probe_jev,
    probe_market,
)
from ai_trader.integrations.fyers.probe import probe_fyers
from ai_trader.journal.service import DecisionJournal, DecisionRecord
from ai_trader.persistence.control_models import ensure_control_tables
from ai_trader.persistence.control_store import ControlStore
from ai_trader.persistence.database import create_database_engine
from ai_trader.persistence.repositories import SqlDecisionJournal
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope

GATE_MARKET = "market test"
GATE_AI = "ai test"
GATE_JEV = "jev test"


@dataclass
class GateResult:
    passed: bool
    lines: list[str]


async def run_integration_gate(
    settings: Settings, symbols: list[str] | None = None
) -> tuple[GateResult, GateResult, GateResult]:
    """Run all three independent probes. No mock fallback is permitted here."""
    market = await probe_configured_market(settings, symbols)
    ai = await probe_ai(settings)
    if not settings.jev_enabled:
        jev = GateResult(True, ["jev: disabled by configuration (JEV_ENABLED=false)"])
    else:
        jev_probe = await probe_jev(settings)
        jev = GateResult(jev_probe.ok, jev_probe.lines)
    return (
        GateResult(market[0], market[1]),
        GateResult(ai.ok, ai.lines),
        jev,
    )


async def probe_configured_market(
    settings: Settings, symbols: list[str] | None = None
) -> tuple[bool, list[str]]:
    """Dispatch the market probe to the configured provider."""
    if settings.market_provider == "fyers":
        result = await probe_fyers(settings, symbols)
        return result.ok, result.lines
    upstox = await probe_market(settings, symbols[0] if symbols else None)
    return upstox.ok, upstox.lines


def format_gate(label: str, result: GateResult) -> str:
    header = f"{label}: {'PASS' if result.passed else 'FAIL'}"
    return "\n".join([header, *(f"  {line}" for line in result.lines)])


async def run_once_shadow(
    settings: Settings, symbols: list[str] | None = None
) -> tuple[DecisionRecord, DecisionJournal, MarketState]:
    """Execute exactly one portfolio review against real market data and persist it."""
    if settings.market_provider == "fyers":
        market = await probe_fyers(settings, symbols)
        market_states = market.states
    else:
        legacy = await probe_market(settings, symbols[0] if symbols else None)
        market_states = [legacy.state] if legacy.state else []
    now = SystemClock().now()
    board = [
        price_state
        for price_state in sorted(market_states, key=lambda s: s.instrument.symbol)
        if stale_data_reason(price_state, now, settings.max_market_data_age_seconds) is None
    ]
    if not board:
        if not market_states:
            raise RuntimeError(
                f"{GATE_MARKET} did not produce a decoded MarketState; refusing to run the workflow"
            )
        raise RuntimeError(f"refusing to run blind: {stale_data_reason(market_states[0], now, settings.max_market_data_age_seconds)}")
    state = board[0]

    clock = SystemClock()

    engine = create_database_engine(settings.database_url)
    journal: DecisionJournal = SqlDecisionJournal(engine)
    ensure_control_tables(engine)
    capital_cap_disabled = ControlStore(engine).get_capital_cap_disabled_sync(
        settings.capital_cap_disabled
    )
    position_count_cap_disabled = ControlStore(engine).get_position_count_cap_disabled_sync(
        settings.position_count_cap_disabled
    )

    # Hard execution isolation: PaperBroker only, regardless of any broker configuration.
    broker = PaperBroker(
        settings.shadow_initial_capital_inr,
        clock,
        margin_multiple=settings.paper_margin_multiple,
        charge_rates=charge_rates_for(settings),
    )
    for price_state in market_states:
        broker.set_price(price_state.instrument.symbol, price_state.last_price)
    safety = SafetyEnvelope(
        settings,
        KillSwitch(),
        clock,
        capital_cap_disabled=capital_cap_disabled,
        position_count_cap_disabled=position_count_cap_disabled,
    )

    remote_provider = build_ai_provider(settings, cadence=True)
    provider = RetryingAIProvider(remote_provider, settings.ai_schema_max_retries)

    critic = build_critic(settings)

    workflow = ShadowReviewWorkflow(
        PortfolioManagerAgent(
            provider,
            "v11",
            position_limits=position_limits_text(
                settings,
                capital_cap_disabled=capital_cap_disabled,
                position_count_cap_disabled=position_count_cap_disabled,
            ),
        ),
        critic,
        broker,
        safety,
        ExecutionService(broker),
        journal,
        clock,
    )
    try:
        record = await workflow.run(state, board=board)
    finally:
        await remote_provider.aclose()
        engine.dispose()
    exec_state = next(
        (price_state for price_state in board if price_state.instrument.symbol == (record.symbol or "")),
        state,
    )
    return record, journal, exec_state


def format_acceptance(state: MarketState, record: DecisionRecord, persisted: bool) -> str:
    initial = record.initial_ai_proposal
    initial_proposal = initial.proposals[0] if initial.proposals else None
    final_proposal = (
        record.final_ai_decision.proposals[0]
        if (record.final_ai_decision and record.final_ai_decision.proposals)
        else None
    )

    def action_of(proposal: TradeProposal | None) -> str:
        return proposal.action.value if proposal is not None else "NONE"

    def allocation_of(proposal: TradeProposal | None) -> str:
        value = proposal.capital_allocation_inr if proposal else None
        if value is None:
            return "n/a"
        shares = proposal.quantity_shares if proposal else None
        if shares is None and state.last_price > 0:
            shares = int(value // state.last_price)
        suffix = f" ({shares} shares)" if shares else ""
        return f"{value:.2f} INR{suffix}"

    lines = [
        "MARKET",
        f"  symbol: {state.instrument.symbol}",
        f"  price: {state.last_price}",
        f"  timestamp: {state.recent_ticks[0].exchange_timestamp.isoformat()}",
        "INITIAL AI",
        f"  server: {record.initial_provider_metadata.provider_metadata.get('server')}",
        f"  model: {record.initial_provider_metadata.model}",
        f"  prompt: {record.initial_provider_metadata.prompt_version}",
        f"  action: {action_of(initial_proposal)}",
        f"  allocation: {allocation_of(initial_proposal)}",
        f"  confidence: {initial_proposal.confidence if initial_proposal else 'n/a'}",
        f"  thesis: {initial_proposal.thesis[:160] if initial_proposal else initial.decision_summary[:160]}",
        f"  latency_ms: {record.initial_provider_metadata.latency_ms}",
        "JEV",
        f"  model: {record.jev_provider_metadata.model if record.jev_provider_metadata else 'not called'}",
        f"  agrees: {record.jev_critique.agrees if record.jev_critique else 'n/a'}",
        f"  evidence_strength: {record.jev_critique.score if record.jev_critique else 'n/a'}",
        f"  critique: {record.jev_critique.critique[:160] if record.jev_critique else 'n/a'}",
        f"  latency_ms: {record.jev_provider_metadata.latency_ms if record.jev_provider_metadata else 'n/a'}",
        "FINAL AI",
        f"  action: {action_of(final_proposal)}",
        f"  allocation: {allocation_of(final_proposal)}",
        f"  confidence: {final_proposal.confidence if final_proposal else 'n/a'}",
        f"  reconsideration_requested: {record.reconsideration_requested}",
        f"  reconsideration_performed: {record.reconsideration_performed}",
        "SAFETY",
        f"  result: {'PASS' if record.safety_allowed else ('DENY' if record.safety_allowed is False else 'NOT_APPLICABLE')}",
        f"  reason: {record.safety_reason or 'n/a'}",
        "EXECUTION",
        (
            f"  paper order: {record.orders_generated[0].broker_order_id} "
            f"({record.orders_generated[0].request.quantity} @ {record.orders_generated[0].request.actual_order_value} INR)"
            if record.orders_generated
            else "  no order requested"
        ),
        "JOURNAL",
        f"  decision_id: {record.decision_id}",
        f"  status: {record.status.value}",
        f"  persisted: {'yes' if persisted else 'no'}",
    ]
    if record.failure_reason:
        lines.insert(-1, f"  failure_reason: {record.failure_reason}")
    return "\n".join(lines)
