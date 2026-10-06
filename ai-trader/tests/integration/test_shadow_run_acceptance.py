"""Verifies the one-shot shadow orchestration and acceptance output wiring.

Transport boundaries are simulated here. This proves the code path is correct and does
NOT constitute the real acceptance run, which requires live credentials and Machine B.
"""

from decimal import Decimal

import pytest

from ai_trader.application import shadow_run
from ai_trader.application.shadow_run import (
    GateResult,
    format_acceptance,
    run_integration_gate,
    run_once_shadow,
)
from ai_trader.config import Settings
from ai_trader.domain.decisions import (
    CriticAssessment,
    PortfolioAction,
    PortfolioDecision,
    ProviderMetadata,
    TradeProposal,
)
from ai_trader.domain.market import Instrument, MarketState, MarketTick
from ai_trader.domain.orders import OrderSide
from ai_trader.integrations.fyers.probe import (
    CONNECTED_AND_DECODED,
    FyersProbeResult,
    probe_fyers,
)


def _settings(database_url: str = "sqlite:///./unused.db") -> Settings:
    return Settings(
        app_mode="LIVE_SHADOW",
        trading_enabled=True,
        database_url=database_url,
        ai_base_url="http://192.168.1.25:8080/v1",
        ai_model="server-model-id",
        ai_api_key="top-secret-key",
        jev_enabled=True,
        jev_api_key="jev-secret-key",
        jev_model="jev-latest",
        shadow_symbol="INFY",
    )


def _market_state() -> MarketState:
    from datetime import UTC, datetime

    now = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    tick = MarketTick(
        symbol="INFY",
        exchange="NSE",
        segment="EQ",
        last_price=Decimal("1500.25"),
        exchange_timestamp=now,
        received_timestamp=now,
        processed_timestamp=now,
        raw_payload={"feeds": {"NSE_EQ|INE009A01021": {"ltpc": {"ltp": 1500.25}}}},
    )
    return MarketState(
        snapshot_id="snapshot-acceptance",
        instrument=Instrument(instrument_key="NSE_EQ|INE009A01021", symbol="INFY", exchange="NSE"),
        timestamp=now,
        last_price=Decimal("1500.25"),
        previous_close=Decimal("1490.10"),
        recent_ticks=[tick],
    )


def _ok_market_result() -> FyersProbeResult:
    """FYERS-shaped probe result carrying a decoded market state."""
    return FyersProbeResult(
        True,
        CONNECTED_AND_DECODED,
        [_market_state()],
        ["provider: fyers", "normalization: PASS"],
    )


@pytest.fixture
def db_url(tmp_path):
    """Isolated SQLite file with the migrated schema, cleaned up by pytest."""
    from sqlalchemy import create_engine

    from ai_trader.persistence.models import Base

    url = f"sqlite:///{tmp_path / 'acceptance.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


async def test_market_probe_fails_closed_without_credential() -> None:
    result = await probe_fyers(Settings(), ["NSE:INFY-EQ"])
    assert result.ok is False
    assert result.states == []
    assert any("credential" in line for line in result.lines)


async def test_gate_blocks_workflow_when_a_probe_fails(monkeypatch) -> None:
    async def fake_market(settings, symbols=None):
        return FyersProbeResult(False, "PROBE_FAILED", [], ["credential: missing"])

    async def fake_ai(settings):
        from ai_trader.integrations.connectivity import AIProbeResult

        return AIProbeResult(True, ["reachable: yes"])

    async def fake_jev(settings):
        from ai_trader.integrations.connectivity import AIProbeResult

        return AIProbeResult(False, ["JEV CONFIG: JEV_API_KEY is not configured"])

    monkeypatch.setattr(shadow_run, "probe_market", fake_market)
    monkeypatch.setattr(shadow_run, "probe_ai", fake_ai)
    monkeypatch.setattr(shadow_run, "probe_jev", fake_jev)

    market, ai, jev = await run_integration_gate(_settings(), ["NSE:INFY-EQ"])
    assert market.passed is False
    assert ai.passed is True
    assert jev.passed is False
    # The gate is conjunctive: one failure blocks the run.
    assert not (market.passed and ai.passed and jev.passed)


async def test_one_shadow_run_persists_three_stages_and_formats_summary(
    monkeypatch, db_url
) -> None:
    decision = PortfolioDecision(
        cash_percent=85.0,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="p-1",
                symbol="INFY",
                action=PortfolioAction.OPEN_POSITION,
                side=OrderSide.BUY,
                capital_allocation_inr=Decimal("15002.50"),
                confidence=0.71,
                thesis="Momentum with volume confirmation.",
                evidence=["live V3 LTP 1500.25"],
                conditions_that_change_thesis=["index gap down"],
            )
        ],
        market_summary="INFY near session high on rising volume.",
        decision_summary="Open a measured long position.",
    )
    reconsidered = decision.model_copy(
        update={
            "proposals": [
                decision.proposals[0].model_copy(
                    update={"capital_allocation_inr": Decimal("6001.00"), "confidence": 0.55}
                )
            ]
        }
    )
    calls = {"n": 0}

    async def fake_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        calls["n"] += 1
        payload = decision if calls["n"] == 1 else reconsidered
        return payload, ProviderMetadata(
            provider="remote_openai_compatible",
            model="server-model-id",
            request_id="req-real-shape",
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=1234,
            input_tokens=512,
            output_tokens=128,
            provider_metadata={
                "server": "192.168.1.25:8080",
                "schema_validation": "passed",
                "retry_count": 0,
            },
        )

    async def fake_assess(self, proposal, news=None):
        return (
            CriticAssessment(
                score=0.44,
                agrees=False,
                disagreement_signals=["allocation too large relative to evidence"],
                critique="Reduce the allocation by half.",
                bounded_decisions=["REQUEST_RECONSIDERATION"],
            ),
            ProviderMetadata(
                provider="typesafe-jev",
                model="jev-latest",
                prompt_name="critic",
                prompt_version="v1",
                latency_ms=890,
            ),
        )

    monkeypatch.setattr(
        "ai_trader.ai.remote_model.OpenAICompatibleAIProvider.structured_completion",
        fake_completion,
    )
    monkeypatch.setattr("ai_trader.ai.jev.TypeSafeJevCritic.assess", fake_assess)

    settings = _settings(db_url)

    async def fake_probe(*args, **kwargs):
        return _ok_market_result()

    monkeypatch.setattr("ai_trader.application.shadow_run.probe_fyers", fake_probe)
    record, journal, state = await run_once_shadow(settings, ["NSE:INFY-EQ"])

    assert calls["n"] == 2
    assert record.reconsideration_requested is True
    assert record.reconsideration_performed is True

    stored = await journal.get(record.decision_id)
    assert stored is not None, "decision must be readable back from SQL"
    assert stored.initial_ai_proposal.proposals[0].capital_allocation_inr == Decimal("15002.50")
    assert stored.final_ai_decision.proposals[0].capital_allocation_inr == Decimal("6001.00")
    assert stored.jev_critique is not None
    assert stored.orders_generated[0].request.quantity == 4
    # AI model and server are searchable DB columns, mirrored from provider metadata.
    assert stored.initial_provider_metadata.model == "server-model-id"
    assert stored.initial_provider_metadata.provider_metadata["server"] == "192.168.1.25:8080"
    assert stored.market_snapshot_id == "snapshot-acceptance"

    summary = format_acceptance(state, record, stored is not None)
    assert "MARKET" in summary
    assert "INITIAL AI" in summary
    assert "JEV" in summary
    assert "FINAL AI" in summary
    assert "SAFETY" in summary
    assert "EXECUTION" in summary
    assert "JOURNAL" in summary
    assert "persisted: yes" in summary
    assert "192.168.1.25:8080" in summary
    # No credentials may appear anywhere in the report.
    assert "top-secret-key" not in summary
    assert "jev-secret-key" not in summary


async def test_hold_outcome_is_reported_as_success_without_order(monkeypatch, db_url) -> None:
    hold = PortfolioDecision(
        cash_percent=100.0,
        allocations=[],
        proposals=[],
        market_summary="No edge.",
        decision_summary="Stay in cash.",
    )

    async def fake_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        return hold, ProviderMetadata(
            provider="remote_openai_compatible",
            model="server-model-id",
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=700,
            provider_metadata={"server": "192.168.1.25:8080"},
        )

    async def fake_assess(self, proposal, news=None):
        return (
            CriticAssessment(
                score=0.95,
                agrees=True,
                disagreement_signals=[],
                critique="Agree.",
                bounded_decisions=["APPROVE"],
            ),
            ProviderMetadata(
                provider="typesafe-jev",
                model="jev-latest",
                prompt_name="critic",
                prompt_version="v1",
                latency_ms=400,
            ),
        )

    monkeypatch.setattr(
        "ai_trader.ai.remote_model.OpenAICompatibleAIProvider.structured_completion",
        fake_completion,
    )
    monkeypatch.setattr("ai_trader.ai.jev.TypeSafeJevCritic.assess", fake_assess)

    async def fake_probe(*args, **kwargs):
        return _ok_market_result()

    monkeypatch.setattr("ai_trader.application.shadow_run.probe_fyers", fake_probe)
    record, journal, state = await run_once_shadow(_settings(db_url), ["NSE:INFY-EQ"])
    stored = await journal.get(record.decision_id)

    assert stored is not None
    assert stored.orders_generated == []
    assert stored.initial_ai_proposal.cash_percent == 100.0
    summary = format_acceptance(state, record, stored is not None)
    assert "no order requested" in summary
    assert "NOT_APPLICABLE" in summary


def test_gate_formatting_is_readable() -> None:
    rendered = shadow_run.format_gate("market test", GateResult(False, ["credential: missing"]))
    assert rendered.startswith("market test: FAIL")
    assert "  credential: missing" in rendered
