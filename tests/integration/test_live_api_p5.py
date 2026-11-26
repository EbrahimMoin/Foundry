"""Live Postgres integration test for Daemon API + WebSocket `?after_seq=` outbox replay."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from foundry.api import DaemonState, create_app
from foundry.blackboard import (
    BlackboardDB,
    EventDispatcher,
    EventRelay,
    PostgresBlackboardRepository,
    resolve_dsn,
    run_migrations,
)
from foundry.decisions import DecisionService


@pytest.mark.live_docker
def test_live_postgres_api_and_ws_after_seq(tmp_path: Path) -> None:
    dsn = resolve_dsn()
    asyncio.run(run_migrations(dsn))

    seeded_run_id: list[str] = []
    db = BlackboardDB(dsn)
    repo = PostgresBlackboardRepository(db)
    dispatcher = EventDispatcher(repo)
    relay = EventRelay(repo, dispatcher)
    decisions = DecisionService(repo)
    state = DaemonState(
        repo=repo,
        dispatcher=dispatcher,
        relay=relay,
        decisions=decisions,
        config_dir=tmp_path / "cfg",
        project_dir=tmp_path / "proj",
    )
    app = create_app(state)
    orig_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _test_lifespan(target_app: FastAPI) -> AsyncIterator[None]:
        async with orig_lifespan(target_app):
            run = await repo.create_run(project_id="live_p5_proj", budget_usd=5.0, phase="building")
            await repo.upsert_feature(
                run.id, "bookmarks", status="in_progress", dispute_rounds_delta=1
            )
            await relay.flush_once()
            seeded_run_id.append(run.id)
            try:
                yield
            finally:
                await db.aclose()

    app.router.lifespan_context = _test_lifespan

    with TestClient(app) as client:
        run_id = seeded_run_id[0]
        metrics = client.get(f"/api/runs/{run_id}/metrics").json()
        assert metrics["dispute_rounds_total"] == 1

        with client.websocket_connect(f"/ws/runs/{run_id}?after_seq=0") as ws:
            first = ws.receive_json()
            assert first["run_id"] == run_id
            assert first["publish_seq"] >= 1
            pause_resp = client.post(f"/api/runs/{run_id}/pause")
            assert pause_resp.status_code == 200
            second = ws.receive_json()
            assert second["publish_seq"] > first["publish_seq"]
