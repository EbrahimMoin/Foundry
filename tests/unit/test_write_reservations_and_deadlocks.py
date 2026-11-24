"""Unit tests for prefix-aware write_reservations, heartbeats, TTL reclaim & wait deadlocks."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from foundry.blackboard import InMemoryBlackboardRepository, TaskStatus
from foundry.concurrency import WriteReservationManager


@pytest.mark.asyncio
async def test_prefix_aware_write_reservations_and_ttl_reclaim() -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="res_test", budget_usd=5.0)
    t_dir = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="bookmarks",
        title="Backend bookmarks",
        description="Feature bookmarks",
        write_set=["backend/app/features/bookmarks/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )
    t_file = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="bookmarks",
        title="Child file inside bookmarks",
        description="Should collide with dir prefix",
        write_set=["backend/app/features/bookmarks/router.py"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )
    t_sib = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="tags",
        title="Sibling tags",
        description="Non-overlapping directory",
        write_set=["backend/app/features/tags/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )

    mgr = WriteReservationManager(repo, default_ttl_seconds=600)
    granted, conflicts = await mgr.acquire(run.id, t_dir.id, ["backend/app/features/bookmarks/"])
    assert len(granted) == 1
    assert conflicts == []

    # File inside reserved dir is denied
    granted_file, conflicts_file = await mgr.acquire(
        run.id, t_file.id, ["backend/app/features/bookmarks/router.py"]
    )
    assert granted_file == []
    assert len(conflicts_file) == 1
    assert conflicts_file[0].holder_task_id == t_dir.id

    # Non-overlapping sibling directory succeeds concurrently
    granted_sib, conflicts_sib = await mgr.acquire(run.id, t_sib.id, ["backend/app/features/tags/"])
    assert len(granted_sib) == 1
    assert conflicts_sib == []

    # Reserve exact file first then request parent directory -> also detects prefix overlap
    await mgr.release(run.id, t_dir.id)
    g2, c2 = await mgr.acquire(run.id, t_file.id, ["backend/app/features/bookmarks/router.py"])
    assert len(g2) == 1 and c2 == []
    g3, c3 = await mgr.acquire(run.id, t_dir.id, ["backend/app/features/bookmarks/"])
    assert g3 == [] and len(c3) == 1 and c3[0].holder_task_id == t_file.id

    # Simulate TTL expiry: expired locks cannot block new acquisitions
    repo._reservations[-1] = repo._reservations[-1].model_copy(
        update={"expires_at": repo._reservations[-1].expires_at - timedelta(hours=1)}
    )
    g4, c4 = await mgr.acquire(run.id, t_dir.id, ["backend/app/features/bookmarks/"])
    assert len(g4) == 1 and c4 == []


@pytest.mark.asyncio
async def test_task_scoped_heartbeat_across_active_states_and_release() -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="hb_test", budget_usd=5.0)
    t = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="notes",
        title="Notes API",
        description="Build notes",
        write_set=["backend/app/features/notes/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.IN_PROGRESS,
    )
    mgr = WriteReservationManager(repo, default_ttl_seconds=100, heartbeat_interval_seconds=0.02)
    await mgr.acquire(run.id, t.id, ["backend/app/features/notes/"])

    for active_status in (TaskStatus.IN_REVIEW, TaskStatus.VERIFIED, TaskStatus.QUEUED):
        t = await repo.set_task_status(t.id, active_status, t.version)
        hb_task = mgr.start_heartbeat(run.id, interval_s=0.01, ttl_seconds=900)
        await asyncio.sleep(0.03)
        await mgr.stop_heartbeat(hb_task)
        locks = await mgr.list_active(run.id)
        assert len(locks) == 1
        assert locks[0].expires_at > locks[0].heartbeat_at + timedelta(seconds=500)

    released = await mgr.release(run.id, t.id, reason="merged")
    assert released == 1
    assert await mgr.list_active(run.id) == []


@pytest.mark.asyncio
async def test_deadlock_cycle_detection() -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="deadlock_test", budget_usd=5.0)
    t1 = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="a",
        title="Task A",
        description="A",
        write_set=["backend/app/features/a/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.BLOCKED,
    )
    t2 = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="frontend",
        feature="a",
        title="Task B",
        description="B",
        write_set=["frontend/src/features/a/"],
        depends_on_task_ids=[t1.id],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )

    mgr = WriteReservationManager(repo)
    await mgr.acquire(run.id, t1.id, ["backend/app/features/a/"])
    # Create wait-for cycle: t2 (READY) wants t1's path while t1 (BLOCKED) depends on t2
    repo._deps[t1.id] = [t2.id]
    repo._write_sets[t2.id] = ["backend/app/features/a/"]

    report = await mgr.detect_wait_cycles(run.id)
    assert report.has_cycle is True
    assert set(report.cycle_task_ids) == {t1.id, t2.id}
