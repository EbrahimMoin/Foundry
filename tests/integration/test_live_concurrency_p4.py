"""Live Postgres & Docker integration tests for Module 3 Phase P4 + A8 hardening."""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from foundry.blackboard import (
    BlackboardDB,
    EventDispatcher,
    EventRelay,
    PostgresBlackboardRepository,
    TaskRecord,
    TaskStatus,
    run_migrations,
)
from foundry.concurrency import SerialMergeQueue, WriteReservationManager
from foundry.config import RuntimeConfig
from foundry.sandbox import (
    HeavyExecSemaphore,
    LocalDockerProvider,
    LogicalDBManager,
    sync_worktree_to_workspace,
)
from foundry.security import HardenedGit, validate_workspace_tree
from foundry.stack_packs import FastAPIReactPostgresPack
from foundry.supervisor import ParallelWaveScheduler
from foundry.verification.models import GateName, GateReport

_BLACKBOARD_DSN = os.environ.get(
    "FOUNDRY_TEST_BLACKBOARD_DSN",
    "postgresql://foundry:foundry_local_dev@127.0.0.1:5433/foundry_blackboard",
)


@pytest.mark.live_docker
@pytest.mark.asyncio
async def test_live_postgres_0002_advisory_reservations_and_ordered_outbox() -> None:
    await run_migrations(_BLACKBOARD_DSN)
    db = BlackboardDB(_BLACKBOARD_DSN)
    repo = PostgresBlackboardRepository(db)

    try:
        run = await repo.create_run(
            project_id=f"p4_live_{uuid.uuid4().hex[:6]}",
            budget_usd=5.0,
        )
        feat = await repo.upsert_feature(
            run.id,
            "bookmarks",
            status="in_progress",
            budget_blackbox_tests_usd=0.30,
        )
        assert feat.name == "bookmarks"
        assert len(await repo.list_features(run.id)) == 1

        # kind='verify' must satisfy 0002 ck_tasks_kind constraint
        t_verify = await repo.create_task(
            run_id=run.id,
            wave=2,
            domain="verifier",
            feature="bookmarks",
            kind="verify",
            title="Verify bookmarks",
            description="Black-box G3 verification task",
            budget_implement_usd=0.30,
            budget_review_usd=0.15,
            status=TaskStatus.PENDING,
        )
        assert t_verify.kind == "verify"

        t_dir = await repo.create_task(
            run_id=run.id,
            wave=2,
            domain="backend",
            feature="bookmarks",
            title="Bookmarks dir owner",
            description="Dir",
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
            title="Bookmarks router file",
            description="File",
            write_set=["backend/app/features/bookmarks/router.py"],
            budget_implement_usd=0.60,
            budget_review_usd=0.15,
            status=TaskStatus.READY,
        )

        # Concurrent advisory-locked acquisition: strictly ONE wins overlapping prefix!
        res_mgr = WriteReservationManager(repo)
        (g_dir, c_dir), (g_file, c_file) = await asyncio.gather(
            res_mgr.acquire(run.id, t_dir.id, ["backend/app/features/bookmarks/"]),
            res_mgr.acquire(run.id, t_file.id, ["backend/app/features/bookmarks/router.py"]),
        )
        winners = int(len(g_dir) == 1) + int(len(g_file) == 1)
        losers = int(len(c_dir) >= 1) + int(len(c_file) >= 1)
        assert winners == 1 and losers == 1

        winner_id = t_dir.id if len(g_dir) == 1 else t_file.id
        renewed = await repo.renew_write_reservations(run.id, task_ids=[winner_id], ttl_seconds=900)
        assert renewed == 1
        released = await res_mgr.release(run.id, winner_id, reason="merged")
        assert released == 1
        assert await res_mgr.list_active(run.id) == []

        # Ordered CTE outbox (A8-4) yields strictly increasing publish_seq matching id order
        for idx in range(5):
            await repo.emit_event(
                run_id=run.id,
                event_type="p4.ordered_check",
                subject_id=str(idx),
                payload={"idx": idx},
            )
        relay = EventRelay(repo, EventDispatcher(repo))
        published = await relay.flush_once(limit=500)
        seqs = [int(ev.publish_seq or 0) for ev in published]
        assert seqs == sorted(seqs)

        # DROP DATABASE ... WITH (FORCE) re-clone check (A8-5)
        mgr = LogicalDBManager(_BLACKBOARD_DSN)
        await mgr.startup_cleanup()
        sbx_db1 = await mgr.create_sandbox_db("live_p4_force")
        host_url = sbx_db1.database_url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "@foundry-postgres:5432/", "@127.0.0.1:5433/"
        )
        active_conn = await asyncpg.connect(host_url)
        try:
            # Re-cloning while an active session holds live_p4_force succeeds via WITH (FORCE)
            await mgr.create_sandbox_db("live_p4_force")
        finally:
            with contextlib.suppress(Exception):
                await active_conn.close()
            await mgr.drop_sandbox_db("live_p4_force")
    finally:
        await db.aclose()


@pytest.mark.live_docker
@pytest.mark.asyncio
async def test_live_docker_a8_writable_volumes_zero_symlinks_and_serial_merge(
    tmp_path: Path,
) -> None:
    await run_migrations(_BLACKBOARD_DSN)
    db = BlackboardDB(_BLACKBOARD_DSN)
    repo = PostgresBlackboardRepository(db)
    pack = FastAPIReactPostgresPack()
    git = HardenedGit()
    sem = HeavyExecSemaphore(2)
    provider = LocalDockerProvider(RuntimeConfig(), sem)

    repo_dir = tmp_path / "repo"
    wt_root = tmp_path / "worktrees"
    ws_root = tmp_path / "workspaces"
    pack.scaffold(repo_dir, app_name="P4Live")
    await git.init_repo(repo_dir)
    await git.commit(
        repo_dir,
        "wave0 init",
        [
            "backend/",
            "frontend/",
            "README.md",
            ".gitignore",
            ".env.example",
            ".github/",
            ".spectral.yaml",
            "contracts/",
            "e2e/",
            "infra/",
        ],
        allow_infra=True,
    )

    run = await repo.create_run(
        project_id=f"p4_docker_{uuid.uuid4().hex[:6]}",
        budget_usd=5.0,
    )
    res_mgr = WriteReservationManager(repo)
    relay = EventRelay(repo, EventDispatcher(repo))

    # Create two non-overlapping Wave 2 backend tasks
    t_notes = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="notes",
        title="Add notes router",
        description="Notes endpoint",
        write_set=["backend/app/features/notes/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )
    t_tags = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="tags",
        title="Add tags router",
        description="Tags endpoint",
        write_set=["backend/app/features/tags/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )

    class _DockerG5Verifier:
        async def verify_combined(
            self,
            *,
            worktree_dir: Path,
            workspace_dir: Path,
            task: TaskRecord,
            base_sha: str,
            allowed_write_set: Sequence[str],
        ) -> Any:
            sbx = await provider.create(
                f"g5_{task.id[-6:]}",
                pack.sandbox_image,
                workspace_dir,
                {},
            )
            try:
                # Verify A8-2: container volumes writable under --read-only rootfs
                nm_link = "/workspace/frontend/node_modules/in_container_symlink"
                vol_chk = await provider.exec(
                    sbx,
                    [
                        "sh",
                        "-c",
                        (
                            "touch /opt/venv/.p4_ok && rm /opt/venv/.p4_ok && "
                            f"ln -s /etc/passwd {nm_link}"
                        ),
                    ],
                )
                assert vol_chk.exit_code == 0, vol_chk.stderr
                validate_workspace_tree(workspace_dir, ignore_git_dir=False)

                # Run lightweight ruff + openapi export inside Docker container + diff guard
                g1_res = await provider.exec(
                    sbx,
                    ["ruff", "check", "backend/"],
                    heavy=False,
                )
                assert g1_res.exit_code == 0, g1_res.stdout + g1_res.stderr
                await git.enforce_diff_write_set(worktree_dir, base_sha, allowed_write_set)
                return GateReport(gate=GateName.G5, passed=True)
            finally:
                await provider.destroy(sbx)

    async def _worker(task: TaskRecord, context: dict[str, Any]) -> dict[str, str]:
        wt = wt_root / task.id
        ws = ws_root / task.id
        branch = task.branch or f"task/{task.id}"
        base_sha = await git.head_sha(repo_dir, "main")
        await git.create_worktree(repo_dir, branch, base_sha, wt)
        sync_worktree_to_workspace(wt, ws)
        await repo.update_task_metadata(task.id, task.version, branch=branch, worktree_path=str(wt))
        feat = task.feature or "core"
        fpath = wt / "backend" / "app" / "features" / feat / "router.py"
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(f'FEATURE = "{feat}"\n', encoding="utf-8")
        await git.commit(wt, f"feat({feat}): router", list(context["write_set"]))
        return {"status": "verified_g2"}

    mq = SerialMergeQueue(
        repo,
        git,
        sem,
        res_mgr,
        repo_dir=repo_dir,
        worktrees_root=wt_root,
        workspaces_root=ws_root,
        g5_verifier=_DockerG5Verifier(),
    )
    scheduler = ParallelWaveScheduler(
        repo,
        res_mgr,
        mq,
        relay,
        worker_fn=_worker,
        max_concurrent_tasks=4,
    )

    try:
        outcomes = await scheduler.run_wave(run.id, wave=2, contracts={"api": {"paths": {}}})
        assert len(outcomes) == 2
        assert all(o.status == "merged" for o in outcomes)
        assert (repo_dir / "backend" / "app" / "features" / "notes" / "router.py").is_file()
        assert (repo_dir / "backend" / "app" / "features" / "tags" / "router.py").is_file()
        assert await res_mgr.list_active(run.id) == []
        _ = (t_notes, t_tags)
    finally:
        await db.aclose()
