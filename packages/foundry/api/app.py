"""FastAPI application factory for Foundry Daemon API, WebSockets, and Dashboard v0.5."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from foundry.api.routes import api_router
from foundry.api.state import DaemonState
from foundry.api.ws import ws_router


def create_app(state: DaemonState) -> FastAPI:
    """Construct the FastAPI daemon application bound to `state`."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        stop_event = asyncio.Event()
        relay_task: asyncio.Task[None] | None = None
        if state.start_relay_in_lifespan:
            relay_task = asyncio.create_task(
                state.relay.run_forever(stop_event, listen_dsn=state.listen_dsn),
                name="foundry-outbox-relay",
            )
        try:
            yield
        finally:
            stop_event.set()
            if relay_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await relay_task

    app = FastAPI(
        title="Foundry Daemon API",
        version="0.5.0",
        lifespan=lifespan,
    )
    app.state.daemon = state

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:4200",
            "http://localhost:4200",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.include_router(ws_router)

    dash_dir = state.dashboard_dir
    if dash_dir is not None and (dash_dir / "index.html").is_file():
        assets_dir = dash_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="dashboard-assets")

        index_file = dash_dir / "index.html"

        @app.get("/", include_in_schema=False)
        async def serve_dashboard_root() -> FileResponse:
            return FileResponse(index_file)

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_dashboard_spa(full_path: str) -> FileResponse:
            if full_path.startswith("api/") or full_path == "api" or full_path.startswith("ws/"):
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (dash_dir / full_path).resolve()
            if candidate.is_file() and dash_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index_file)

    return app
