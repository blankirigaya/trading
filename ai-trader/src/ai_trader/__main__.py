import argparse
import asyncio
from pathlib import Path

from ai_trader.application.container import Container
from ai_trader.application.fyers_session import build_session_from_settings, summarise_portfolio
from ai_trader.application.shadow_run import (
    format_acceptance,
    format_gate,
    run_integration_gate,
    run_once_shadow,
)
from ai_trader.config import Settings
from ai_trader.demo import run_demo
from ai_trader.integrations.connectivity import probe_ai, probe_jev, probe_market
from ai_trader.integrations.fyers.probe import probe_fyers
from ai_trader.replay.service import ReplayService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai_trader")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="Run the deterministic Phase 1 paper demo")

    replay = sub.add_parser("replay", help="Replay the stored fixture dataset")
    replay.add_argument("--date", default="2026-09-01")

    market = sub.add_parser("market", help="Verify market data connectivity")
    market.add_argument("action", choices=["test"], default="test", nargs="?")
    market.add_argument("--provider", default=None)
    market.add_argument("--symbol", default=None)

    ai = sub.add_parser("ai", help="Verify the remote AI inference service")
    ai.add_argument("action", choices=["test"], default="test", nargs="?")

    jev = sub.add_parser("jev", help="Verify the TypeSafe AI Jev critic")
    jev.add_argument("action", choices=["test"], default="test", nargs="?")

    fyers = sub.add_parser("fyers", help="FYERS market data commands")
    fyers.add_argument("action", choices=["test"], default="test", nargs="?")
    fyers.add_argument("--symbol", default=None)

    news = sub.add_parser("news", help="News intelligence commands")
    news.add_argument("action", choices=["test", "poll", "analyze"], default="test", nargs="?")
    news.add_argument("--once", action="store_true", help="Poll a single time")
    news.add_argument("--latest", action="store_true", help="Analyse the newest items")
    news.add_argument("--limit", type=int, default=5)

    shadow = sub.add_parser("shadow", help="Run the AI paper-trading session")
    shadow.add_argument("--provider", default="fyers", choices=["fyers"])
    shadow.add_argument("--symbol", action="append", default=None)
    shadow.add_argument("--once", action="store_true", help="Run exactly one review and exit")
    shadow.add_argument("--reviews", type=int, default=None, help="Stop after N reviews")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()

    if args.command == "demo":
        asyncio.run(run_demo())
        return 0

    if args.command == "replay":
        app = Container()
        asyncio.run(
            ReplayService(app.bus, app.orchestrator).replay_file(
                Path("data/replay/demo_events.jsonl")
            )
        )
        return 0

    if args.command == "market":
        provider = args.provider or settings.market_provider
        if provider == "fyers":
            result = asyncio.run(probe_fyers(settings, [args.symbol] if args.symbol else None))
            print("\n".join(result.lines))
            return 0 if result.ok else 1
        upstox_result = asyncio.run(probe_market(settings, args.symbol))
        print("\n".join(upstox_result.lines))
        return 0 if upstox_result.ok else 1

    if args.command == "ai":
        ai_result = asyncio.run(probe_ai(settings))
        print("\n".join(ai_result.lines))
        return 0 if ai_result.ok else 1

    if args.command == "jev":
        jev_result = asyncio.run(probe_jev(settings))
        print("\n".join(jev_result.lines))
        return 0 if jev_result.ok else 1

    if args.command == "fyers":
        result = asyncio.run(probe_fyers(settings, [args.symbol] if args.symbol else None))
        print("\n".join(result.lines))
        return 0 if result.ok else 1

    if args.command == "news":
        from ai_trader.news.cli import news_analyse_latest, news_poll_once, news_test

        if args.action == "test":
            return news_test(settings)
        if args.action == "poll":
            return news_poll_once(settings)
        if args.action == "analyze":
            return news_analyse_latest(settings, limit=args.limit)
        return 1

    if args.command == "shadow":
        return asyncio.run(_shadow(settings, args))

    return 1


async def _shadow(settings: Settings, args: argparse.Namespace) -> int:
    symbols = args.symbol or None

    if args.once:
        market_gate, ai_gate, jev_gate = await run_integration_gate(settings, symbols)
        print(format_gate("market test", market_gate))
        print(format_gate("ai test", ai_gate))
        print(format_gate("jev test", jev_gate))
        if not (market_gate.passed and ai_gate.passed and jev_gate.passed):
            print("\nINTEGRATION GATE: FAIL - not starting the shadow workflow.")
            return 1
        print("\nINTEGRATION GATE: PASS")
        record, journal, state = await run_once_shadow(settings, symbols)
        persisted = await journal.get(record.decision_id)
        print("")
        print(format_acceptance(state, record, persisted is not None))
        return 0

    session = await build_session_from_settings(settings, symbols)
    print("FYERS shadow session starting (provider=fyers, execution=PaperBroker)")
    print(f"symbols: {', '.join(session.feed.topic_to_symbol.values()) or 'n/a'}")
    print(f"review cadence: {session.interval_seconds}s")
    await session.start_ingestion()
    try:
        results = await session.run_loop(max_reviews=args.reviews)
        for result in results:
            print(f"review {result.record_id} status={result.status}")
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nstopping")
    finally:
        await session.aclose()
    print("")
    print("PORTFOLIO")
    for key, value in summarise_portfolio(session.broker).items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
