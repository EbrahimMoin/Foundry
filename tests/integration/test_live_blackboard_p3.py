"""Live Postgres Blackboard, LISTEN/NOTIFY monotonic publish_seq, and AsyncPostgresSaver tests."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import yaml
from langgraph.types import Command

from foundry.agents.graphs.project_graph import (
    ProjectGraphDeps,
    ProjectState,
    build_project_graph,
)
from foundry.blackboard import (
    BlackboardDB,
    BlackboardEvent,
    EventDispatcher,
    EventRelay,
    PostgresBlackboardRepository,
    StaleWriteError,
    TaskStatus,
    run_migrations,
    to_asyncpg_dsn,
)
from foundry.config import (
    AutonomyConfig,
    AutonomyPreset,
    ProviderConfig,
    default_models_config,
)
from foundry.decisions import DecisionService
from foundry.gateway import (
    CassetteStore,
    GatewayRequest,
    GatewayResponse,
    MissPolicy,
    ModelGateway,
    TransportHealth,
)
from foundry.supervisor import RunSupervisor, create_postgres_saver_context

_BLACKBOARD_DSN = os.environ.get(
    "FOUNDRY_TEST_BLACKBOARD_DSN",
    "postgresql://foundry:foundry_local_dev@127.0.0.1:5433/foundry_blackboard",
)


@pytest.mark.live_docker
@pytest.mark.asyncio
async def test_live_postgres_migrations_out_of_order_publish_seq_and_locking() -> None:
    await run_migrations(_BLACKBOARD_DSN)
    db = BlackboardDB(_BLACKBOARD_DSN)
    repo = PostgresBlackboardRepository(db)

    run = await repo.create_run(
        project_id=f"outbox_{uuid.uuid4().hex[:6]}",
        budget_usd=5.0,
    )
    task = await repo.create_task(
        run_id=run.id,
        wave=1,
        domain="backend",
        title="Outbox lock test",
        description="Verify optimistic version checks",
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )
    updated = await repo.set_task_status(task.id, TaskStatus.IN_PROGRESS, expected_version=1)
    assert updated.version == 2
    with pytest.raises(StaleWriteError):
        await repo.set_task_status(task.id, TaskStatus.IN_REVIEW, expected_version=1)

    received: list[BlackboardEvent] = []
    got_fast = asyncio.Event()
    got_slow = asyncio.Event()

    async def _consumer(ev: BlackboardEvent) -> None:
        if ev.run_id != run.id:
            return
        received.append(ev)
        if ev.type == "outbox.fast_b":
            got_fast.set()
        elif ev.type == "outbox.slow_a":
            got_slow.set()

    dispatcher = EventDispatcher(repo)
    dispatcher.subscribe(f"live_test_{uuid.uuid4().hex[:6]}", _consumer)
    relay = EventRelay(repo, dispatcher)
    await relay.flush_once()

    stop_event = asyncio.Event()
    relay_task = asyncio.create_task(
        relay.run_forever(stop_event, listen_dsn=_BLACKBOARD_DSN, poll_interval_s=0.2)
    )

    pg_dsn = to_asyncpg_dsn(_BLACKBOARD_DSN)
    conn_a = await asyncpg.connect(pg_dsn)
    conn_b = await asyncpg.connect(pg_dsn)
    try:
        txn_a = conn_a.transaction()
        await txn_a.start()
        id_a = await conn_a.fetchval(
            "INSERT INTO events (run_id, type, subject_id, payload) "
            "VALUES ($1, 'outbox.slow_a', $2, '{}'::jsonb) RETURNING id",
            run.id,
            task.id,
        )

        txn_b = conn_b.transaction()
        await txn_b.start()
        id_b = await conn_b.fetchval(
            "INSERT INTO events (run_id, type, subject_id, payload) "
            "VALUES ($1, 'outbox.fast_b', $2, '{}'::jsonb) RETURNING id",
            run.id,
            task.id,
        )
        assert id_b > id_a
        await txn_b.commit()

        await asyncio.wait_for(got_fast.wait(), timeout=5.0)
        fast_seq = next(ev.publish_seq for ev in received if ev.type == "outbox.fast_b")

        await txn_a.commit()
        await asyncio.wait_for(got_slow.wait(), timeout=5.0)
        slow_seq = next(ev.publish_seq for ev in received if ev.type == "outbox.slow_a")

        # Slow earlier-ID transaction gets strictly higher publish_seq than earlier-committed fast_b
        assert fast_seq is not None and slow_seq is not None
        assert slow_seq > fast_seq
    finally:
        stop_event.set()
        await asyncio.wait_for(relay_task, timeout=5.0)
        await conn_a.close()
        await conn_b.close()
        await db.aclose()


class _LiveCountingTransport:
    name = "live_counting"

    def __init__(self) -> None:
        self.node_calls: dict[str, int] = {}

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        node = request.node or "unknown"
        self.node_calls[node] = self.node_calls.get(node, 0) + 1
        if node == "draft_requirements":
            body: dict[str, Any] = {
                "summary": "Live Postgres checkpointer test",
                "features": [{"name": "items", "description": "Items CRUD"}],
                "roles": ["member"],
            }
        else:
            body = {
                "stack_pack": "fastapi-react-postgres",
                "auth_scheme": "bearer_jwt",
                "features": [{"name": "items"}],
                "schema_changes": [],
            }
        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=provider_cfg.transport,
            billing_mode=provider_cfg.billing_mode,
            content=json.dumps(body),
            tokens_in=40,
            tokens_out=25,
        )

    async def health_check(self, provider_id: str, provider_cfg: ProviderConfig) -> TransportHealth:
        return TransportHealth(
            provider_id=provider_id,
            transport=provider_cfg.transport,
            healthy=True,
            detail="live_counting",
        )


class _NoopHooks:
    async def ensure_contract_planning(self, run_id: str, state: dict[str, Any]) -> int | None:
        _ = (run_id, state)
        return 1

    async def ensure_wave1(self, run_id: str, state: dict[str, Any]) -> bool:
        _ = (run_id, state)
        return True

    async def ensure_wave2(self, run_id: str, state: dict[str, Any]) -> bool:
        _ = (run_id, state)
        return True


@pytest.mark.live_docker
@pytest.mark.asyncio
async def test_live_async_postgres_saver_interrupt_and_crash_resume() -> None:
    await run_migrations(_BLACKBOARD_DSN)
    db = BlackboardDB(_BLACKBOARD_DSN)
    repo = PostgresBlackboardRepository(db)
    models_cfg = default_models_config()
    transport_kind = models_cfg.providers["gemini_paid"].transport

    try:
        run = await repo.create_run(
            project_id=f"pgsaver_{uuid.uuid4().hex[:6]}",
            budget_usd=5.0,
        )
        t1 = _LiveCountingTransport()
        gw1 = ModelGateway(models_cfg, transports={transport_kind: t1})
        decisions = DecisionService(
            repo,
            autonomy_cfg=AutonomyConfig(preset=AutonomyPreset.BALANCED),
        )
        cfg = {"configurable": {"thread_id": f"{run.id}:project"}}

        async with create_postgres_saver_context(_BLACKBOARD_DSN) as saver1:
            await saver1.setup()
            g1 = build_project_graph(
                ProjectGraphDeps(
                    gateway=gw1,
                    decisions=decisions,
                    blackboard=repo,
                    supervisor_hooks=_NoopHooks(),
                ),
                checkpointer=saver1,
            )
            init_state: ProjectState = {
                "run_id": run.id,
                "task_slug": "pg_crash_resume",
                "app_name": "pg_crash_resume",
                "spec": "Build items service",
                "req_revision": 0,
                "arch_revision": 0,
                "phase": "planning",
            }
            paused = await g1.ainvoke(init_state, cfg)
            assert "__interrupt__" in paused
            assert t1.node_calls.get("draft_requirements") == 1

        pending = await repo.list_decisions(run_id=run.id, status="pending")
        assert len(pending) == 1
        resolved = await decisions.resolve(pending[0].id, action="approve", decided_by="live_user")

        # Simulate full daemon restart: brand-new AsyncPostgresSaver + brand-new transport
        t2 = _LiveCountingTransport()
        gw2 = ModelGateway(models_cfg, transports={transport_kind: t2})
        async with create_postgres_saver_context(_BLACKBOARD_DSN) as saver2:
            await saver2.setup()
            g2 = build_project_graph(
                ProjectGraphDeps(
                    gateway=gw2,
                    decisions=decisions,
                    blackboard=repo,
                    supervisor_hooks=_NoopHooks(),
                ),
                checkpointer=saver2,
            )
            resumed = await g2.ainvoke(Command(resume=resolved.answer), cfg)
            assert "__interrupt__" in resumed
            # draft_requirements MUST NOT re-execute after crash recovery
            assert t2.node_calls.get("draft_requirements", 0) == 0
            assert t2.node_calls.get("draft_architecture") == 1
    finally:
        await db.aclose()


@pytest.mark.live_docker
@pytest.mark.asyncio
async def test_live_postgres_s1_staged_contracts_and_wave1_to_wave2() -> None:
    await run_migrations(_BLACKBOARD_DSN)
    spec_dir = await asyncio.to_thread(Path("bench/specs/s1_bookmarks").resolve)
    cassette_dir = await asyncio.to_thread(Path("tests/cassettes").resolve)

    spec_text = await asyncio.to_thread((spec_dir / "spec.md").read_text, encoding="utf-8")
    pinned_oa_yaml = await asyncio.to_thread(
        (spec_dir / "pinned_openapi.yaml").read_text, encoding="utf-8"
    )
    pinned_ti_yaml = await asyncio.to_thread(
        (spec_dir / "pinned_testids.yaml").read_text, encoding="utf-8"
    )
    pinned_oa_doc = yaml.safe_load(pinned_oa_yaml)
    pinned_ti = list(yaml.safe_load(pinned_ti_yaml)["required_testids"])

    db = BlackboardDB(_BLACKBOARD_DSN)
    repo = PostgresBlackboardRepository(db)
    cassettes = CassetteStore(cassette_dir, miss_policy=MissPolicy.STRICT)

    try:
        async with create_postgres_saver_context(_BLACKBOARD_DSN) as saver:
            await saver.setup()
            supervisor = RunSupervisor(
                repo,
                autonomy_cfg=AutonomyConfig(preset=AutonomyPreset.AUTOPILOT),
                cassettes=cassettes,
                checkpointer=saver,
            )
            summary = await supervisor.start_run(
                spec=spec_text,
                app_name="s1_bookmarks",
                task_slug="s1_bookmarks",
                pinned_openapi=pinned_oa_doc,
                pinned_openapi_yaml=pinned_oa_yaml,
                pinned_testids=pinned_ti,
                pinned_testids_yaml=pinned_ti_yaml,
            )

        assert summary.status == "completed"
        assert summary.phase == "done"
        assert len(summary.contracts) == 4
        assert all(c.status == "locked" for c in summary.contracts)
        assert len([t for t in summary.tasks if t.wave == 1]) == 4
        assert len([t for t in summary.tasks if t.wave == 2]) >= 4
        assert all(t.status == TaskStatus.MERGED for t in summary.tasks)

        events = await repo.list_events_after(0, run_id=summary.run_id)
        event_types = [e.type for e in events]
        assert "contracts.locked" in event_types
        assert event_types.count("wave.completed") == 2
        assert all(e.publish_seq is not None for e in events)
    finally:
        await db.aclose()
