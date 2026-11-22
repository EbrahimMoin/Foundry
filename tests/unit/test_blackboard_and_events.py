"""Offline tests for Blackboard optimistic locking, EventRelay publish_seq, and blackboard-mcp."""

from __future__ import annotations

import pytest

from foundry.blackboard import (
    BlackboardEvent,
    DeterministicHashEmbedder,
    EventDispatcher,
    EventRelay,
    InMemoryBlackboardRepository,
    StaleWriteError,
    TaskStatus,
)
from foundry.mcp_servers import BlackboardMCPServer


@pytest.mark.asyncio
async def test_blackboard_optimistic_locking_and_event_deduplication() -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="bookmarks", budget_usd=5.0)
    task = await repo.create_task(
        run_id=run.id,
        wave=1,
        domain="backend",
        title="Build auth",
        description="Auth endpoints",
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
        write_set=["backend/app/features/auth/"],
    )

    updated = await repo.set_task_status(task.id, TaskStatus.IN_PROGRESS, expected_version=1)
    assert updated.version == 2
    assert updated.status == TaskStatus.IN_PROGRESS

    with pytest.raises(StaleWriteError):
        await repo.set_task_status(task.id, TaskStatus.IN_REVIEW, expected_version=1)

    seen_a: list[tuple[int | None, str]] = []
    seen_b: list[str] = []

    async def _handler_a(ev: BlackboardEvent) -> None:
        seen_a.append((ev.publish_seq, ev.type))

    async def _handler_b(ev: BlackboardEvent) -> None:
        seen_b.append(ev.type)

    dispatcher = EventDispatcher(repo)
    dispatcher.subscribe("group_a", _handler_a)
    dispatcher.subscribe("group_b", _handler_b, event_types=["task.in_progress"])
    relay = EventRelay(repo, dispatcher)

    published = await relay.flush_once()
    assert [e.publish_seq for e in published] == [1, 2, 3]
    assert seen_a == [(1, "run.created"), (2, "task.ready"), (3, "task.in_progress")]
    assert seen_b == ["task.in_progress"]

    # Re-dispatching the exact same events must deduplicate cleanly via processed_events
    for ev in published:
        await dispatcher.dispatch(ev)
    assert len(seen_a) == 3

    # A brand new consumer group joining late replays strictly after last_processed_seq (0)
    seen_late: list[int | None] = []

    async def _handler_late(ev: BlackboardEvent) -> None:
        seen_late.append(ev.publish_seq)

    dispatcher.subscribe("group_late", _handler_late)
    replayed = await dispatcher.replay_missed("group_late", run_id=run.id)
    assert replayed == 3
    assert seen_late == [1, 2, 3]


@pytest.mark.asyncio
async def test_blackboard_mcp_server_tools_and_embedder() -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="demo", budget_usd=5.0)
    task = await repo.create_task(
        run_id=run.id,
        wave=1,
        domain="data",
        title="Schema",
        description="Initial models",
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        write_set=["backend/app/models/"],
    )
    mcp_srv = BlackboardMCPServer(repo)
    fetched = await mcp_srv.get_task(task.id)
    assert fetched["write_set"] == ["backend/app/models/"]

    note = await mcp_srv.post_note(run.id, "gotcha", "Remember UUID PKs", task_id=task.id)
    assert note["kind"] == "gotcha"
    msg = await mcp_srv.post_message(run.id, "thr_1", "lead:backend", "Need index on email")
    assert msg["from_id"] == "lead:backend"

    app = mcp_srv.build_mcp_server()
    tool_names = {t.name for t in await app.list_tools()}
    assert tool_names == {
        "get_task",
        "get_contract",
        "list_decisions",
        "post_note",
        "post_message",
        "propose_contract_change",
    }

    embedder = DeterministicHashEmbedder()
    vecs = await embedder.embed(["hello world", "goodbye world"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 384
