from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_trader.api.news_routes import router as news_router
from ai_trader.api.routes import router
from ai_trader.api.ws import router as ws_router
from ai_trader.application.container import Container
from ai_trader.observability.logging import configure_logging

# The Electron renderer runs on its own origin (the Vite dev server in development,
# file:// once packaged) and calls this API directly, so the browser treats every read
# as cross-origin. The list is explicit rather than a wildcard: only this machine's
# development origins and the packaged file:// origin may call it. No credentials are
# involved, so cookies are not allowed and CORS grants nothing that local access to
# the port does not already grant.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "null",  # origin reported by a file:// document (packaged Electron)
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Release pooled network resources on shutdown.
    await app.state.container.aclose()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="AI Trader Phase 2A", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.state.container = Container()
    app.include_router(router)
    app.include_router(news_router)
    app.include_router(ws_router)
    return app


app = create_app()
