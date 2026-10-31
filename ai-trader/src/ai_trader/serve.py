"""Backend entry point that survives flaky Windows networking.

The default asyncio ProactorEventLoop drives uvicorn's accept loop through
IOCP. When this machine's Wi-Fi adapters flap, the proactor's accept coroutine
dies with WinError 64 and the server silently stops accepting connections
while the process lives on (zombie backend, UI frozen, supervisor restarts,
paper books wiped). The selector loop drives accept through select(), which
does not have this failure mode. Everything else (uvicorn, the app, websockets,
httpx, to_thread) runs identically on either loop.

Setting asyncio.set_event_loop_policy() before uvicorn.run() is not enough:
uvicorn builds its own loop through its loop factory (ProactorEventLoop on
Windows) and ignores the installed policy. So the factory is passed explicitly
via Config(loop=...), which uvicorn calls instead of its own default.

Usage: uv run python -m ai_trader.serve [--host 127.0.0.1] [--port 8000]
"""

import asyncio
import sys

import uvicorn


def selector_loop_factory(use_subprocess: bool = False) -> asyncio.AbstractEventLoop:
    """Build the event loop uvicorn should run on.

    uvicorn calls this with ``use_subprocess=<bool>`` (only true for reload or
    multiprocess serving), so keep the keyword for signature compatibility.
    """
    del use_subprocess
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def main(argv: list[str]) -> int:
    host = "127.0.0.1"
    port = 8000
    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--host" and args:
            host = args.pop(0)
        elif flag == "--port" and args:
            port = int(args.pop(0))
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    config = uvicorn.Config(
        "ai_trader.main:app",
        host=host,
        port=port,
        loop="ai_trader.serve:selector_loop_factory",
    )
    uvicorn.Server(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
