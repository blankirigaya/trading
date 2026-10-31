"""AI token/cost usage aggregation and reconsideration persistence."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ai_trader.domain.decisions import DecisionStatus, PortfolioDecision, ProviderMetadata
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.journal.service import DecisionRecord
from ai_trader.reporting.usage import summarize_usage


def _meta(
    model: str = "m",
    inbound: int | None = 100,
    outbound: int | None = 50,
    cost: float | None = None,
) -> ProviderMetadata:
    extra: dict[str, object] = {}
    if cost is not None:
        extra["estimated_total_usd"] = cost
    return ProviderMetadata(
        provider="bedrock",
        model=model,
        prompt_name="p",
        prompt_version="v1",
        input_tokens=inbound,
        output_tokens=outbound,
        provider_metadata=extra,
    )


def _record(
    decision_id: str,
    when: datetime,
    meta: ProviderMetadata,
    recon: ProviderMetadata | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        correlation_id="c",
        event_id="e",
        timestamp=when,
        status=DecisionStatus.APPROVED_BY_AI,
        input_context_hash="h",
        initial_ai_proposal=PortfolioDecision(
            cash_percent=100.0,
            allocations=[],
            proposals=[],
            market_summary="m",
            decision_summary="d",
        ),
        initial_provider_metadata=meta,
        portfolio_before=PortfolioState(cash=Decimal("100000")),
        reconsideration_performed=recon is not None,
        reconsideration_provider_metadata=recon,
    )


def test_usage_sums_reviews_and_reconsiderations() -> None:
    now = datetime.now(UTC)
    records = [
        _record("a", now, _meta("m1", 100, 50, 0.001), _meta("m1", 200, 60, 0.002)),
        _record("b", now, _meta("m2", 10, 5, None)),
    ]
    summary = summarize_usage(records, [], cost_configured=True)
    assert summary["calls"] == 3
    assert summary["input_tokens"] == 310
    assert summary["output_tokens"] == 115
    assert summary["estimated_cost_usd"] == round(0.001 + 0.002, 6)
    assert summary["cost_configured"] is True
    by_model = summary["by_model"]
    assert isinstance(by_model, dict)
    assert by_model["m1"]["calls"] == 2
    assert by_model["m2"]["input_tokens"] == 10
    sources = summary["sources"]
    assert isinstance(sources, dict)
    assert sources["reviews"]["calls"] == 2
    assert sources["reconsiderations"]["calls"] == 1
    assert sources["news"]["calls"] == 0


def test_usage_ignores_missing_tokens_and_splits_24h_window() -> None:
    now = datetime.now(UTC)
    old = now - timedelta(hours=25)
    records = [
        _record("a", now, _meta("m", None, None, None)),
        _record("b", old, _meta("m", 40, 20, 0.004)),
    ]
    summary = summarize_usage(records, [], cost_configured=False)
    assert summary["calls"] == 2
    assert summary["input_tokens"] == 40
    assert summary["estimated_cost_usd"] == 0.004
    assert summary["cost_configured"] is False
    last_24h = summary["last_24h"]
    assert isinstance(last_24h, dict)
    assert last_24h["calls"] == 1
    assert last_24h["input_tokens"] == 0


def test_usage_counts_news_analyses() -> None:
    now = datetime.now(UTC)
    from ai_trader.domain.news import AnalysisStatus, NewsAnalysis

    analysis = NewsAnalysis(
        analysis_id="n1",
        cluster_id="c1",
        item_id="i1",
        status=AnalysisStatus.COMPLETED,
        analysis_completed_at=now,
        provider_metadata={
            "model": "m1",
            "input_tokens": 500,
            "output_tokens": 60,
            "estimated_total_usd": 0.005,
        },
    )
    summary = summarize_usage([], [analysis], cost_configured=True)
    assert summary["calls"] == 1
    assert summary["input_tokens"] == 500
    assert summary["estimated_cost_usd"] == 0.005


def test_reconsideration_metadata_round_trips_through_sql_journal(tmp_path) -> None:
    import asyncio

    from sqlalchemy import create_engine

    from ai_trader.persistence.models import Base
    from ai_trader.persistence.repositories import SqlDecisionJournal

    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    Base.metadata.create_all(engine)
    journal = SqlDecisionJournal(engine)
    now = datetime.now(UTC)
    record = _record("r1", now, _meta("m", 100, 50, 0.001), _meta("m", 200, 60, 0.002))
    asyncio.run(journal.save(record))
    loaded = asyncio.run(journal.get("r1"))
    assert loaded is not None
    assert loaded.reconsideration_provider_metadata is not None
    assert loaded.reconsideration_provider_metadata.input_tokens == 200
    assert loaded.initial_provider_metadata.output_tokens == 50
