"""Unit tests for SerialMergeQueue (3-tier rebase, G5, pending_deps, schema & A8-1 join)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from foundry.blackboard import InMemoryBlackboardRepository, TaskRecord, TaskStatus
from foundry.concurrency import SerialMergeQueue, UnapprovedDependencyError, WriteReservationManager
from foundry.sandbox import HeavyExecSemaphore, PackageInstallPolicy
from foundry.security import HardenedGit
from foundry.verification.models import GateName, GateReport


async def _init_sample_repo(tmp_path: Path) -> tuple[HardenedGit, Path, str]:
    git = HardenedGit()
    repo_dir = tmp_path / "repo"
    await git.init_repo(repo_dir)
    (repo_dir / "backend" / "app" / "features" / "alpha").mkdir(parents=True)
    (repo_dir / "backend" / "app" / "features" / "alpha" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (repo_dir / "frontend" / "src" / "features" / "alpha").mkdir(parents=True)
    (repo_dir / "frontend" / "src" / "features" / "alpha" / "index.ts").write_text(
        "export {};\n", encoding="utf-8"
    )
    (repo_dir / "backend" / "pyproject.toml").write_text(
        '[project]\nname = "app"\ndependencies = [\n  "fastapi>=0.115",\n]\n',
        encoding="utf-8",
    )
    sha = await git.commit(repo_dir, "wave1 init", ["backend/", "frontend/"], allow_infra=True)
    return git, repo_dir, sha


@pytest.mark.asyncio
async def test_serial_merge_queue_multi_branch_rebase_schema_refresh_and_a8_join(
    tmp_path: Path,
) -> None:
    git, repo_dir, base_sha = await _init_sample_repo(tmp_path)
    wt_root = tmp_path / "worktrees"
    ws_root = tmp_path / "workspaces"
    bb = InMemoryBlackboardRepository()
    run = await bb.create_run(project_id="mq_test", budget_usd=5.0)
    res_mgr = WriteReservationManager(bb)

    # Wave 2: schema barrier + backend build + frontend build for feature "alpha"
    t_schema = await bb.create_task(
        run_id=run.id,
        wave=2,
        domain="data",
        kind="schema",
        title="Add alpha model",
        description="Schema",
        write_set=["backend/app/models/"],
        base_sha=base_sha,
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.VERIFIED,
    )
    t_be = await bb.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="alpha",
        kind="build",
        title="Alpha backend",
        description="BE",
        write_set=["backend/app/features/alpha/"],
        base_sha=base_sha,
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.VERIFIED,
    )
    t_fe = await bb.create_task(
        run_id=run.id,
        wave=2,
        domain="frontend",
        feature="alpha",
        kind="build",
        title="Alpha frontend",
        description="FE",
        write_set=["frontend/src/features/alpha/"],
        base_sha=base_sha,
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.VERIFIED,
    )

    for task, branch, rel_file, content in [
        (t_schema, "task/schema", "backend/app/models/alpha.py", "ALPHA = 1\n"),
        (
            t_be,
            "task/be",
            "backend/app/features/alpha/router.py",
            "ROUTE = '/api/alpha'\n",
        ),
        (
            t_fe,
            "task/fe",
            "frontend/src/features/alpha/AlphaPage.tsx",
            "export const AlphaPage = () => null;\n",
        ),
    ]:
        wt = wt_root / task.id
        await git.create_worktree(repo_dir, branch, base_sha, wt)
        fpath = wt / rel_file
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
        await git.commit(wt, f"feat: {task.id}", await bb.get_task_write_sets(task.id))
        await bb.update_task_metadata(task.id, task.version, branch=branch, worktree_path=str(wt))
        await res_mgr.acquire(run.id, task.id, await bb.get_task_write_sets(task.id))

    # Curated pending_deps applied deterministically to backend task
    be_cur = await bb.get_task(t_be.id)
    await bb.update_task_pending_deps(
        be_cur.id, [{"ecosystem": "python", "package": "slugify==0.0.1"}], be_cur.version
    )

    refreshed_schemas: list[str] = []
    g5_calls: list[str] = []

    class _Refresher:
        async def refresh(self, *, worktree_dir: Path, task: TaskRecord) -> None:
            _ = worktree_dir
            refreshed_schemas.append(task.id)

    class _Verifier:
        async def verify_combined(
            self,
            *,
            worktree_dir: Path,
            workspace_dir: Path,
            task: TaskRecord,
            base_sha: str,
            allowed_write_set: Sequence[str],
        ) -> GateReport:
            _ = (worktree_dir, workspace_dir, base_sha, allowed_write_set)
            g5_calls.append(task.id)
            return GateReport(gate=GateName.G5, passed=True)

    mq = SerialMergeQueue(
        bb,
        git,
        HeavyExecSemaphore(2),
        res_mgr,
        repo_dir=repo_dir,
        worktrees_root=wt_root,
        workspaces_root=ws_root,
        package_policy=PackageInstallPolicy(curated_python_allowlist=frozenset({"slugify"})),
        g5_verifier=_Verifier(),
        schema_refresher=_Refresher(),
    )

    out_schema = await mq.process_task(run.id, t_schema.id)
    assert out_schema.status == "merged"
    assert refreshed_schemas == [t_schema.id]

    out_be = await mq.process_task(run.id, t_be.id)
    assert out_be.status == "merged"
    # A8-1: backend MERGED alone does NOT fire join barrier yet!
    assert out_be.spawned_integration_task_id is None
    assert "slugify==0.0.1" in (repo_dir / "backend" / "pyproject.toml").read_text(encoding="utf-8")

    out_fe = await mq.process_task(run.id, t_fe.id)
    assert out_fe.status == "merged"
    # A8-1: Once BOTH backend and frontend are MERGED, join barrier spawns integration cleanly!
    assert out_fe.spawned_integration_task_id is not None
    integ = await bb.get_task(out_fe.spawned_integration_task_id)
    assert integ.kind == "integration" and integ.status == TaskStatus.READY
    assert g5_calls == [t_schema.id, t_be.id, t_fe.id]

    # Critical A8-1 proof: integration task can immediately acquire BOTH directories!
    integ_ws = await bb.get_task_write_sets(integ.id)
    granted_integ, conflicts_integ = await res_mgr.acquire(run.id, integ.id, integ_ws)
    assert len(granted_integ) == 2 and conflicts_integ == []


@pytest.mark.asyncio
async def test_serial_merge_queue_3_tier_conflict_resolver_and_rerun_fallback(
    tmp_path: Path,
) -> None:
    git, repo_dir, base_sha = await _init_sample_repo(tmp_path)
    wt_root = tmp_path / "worktrees"
    ws_root = tmp_path / "workspaces"
    bb = InMemoryBlackboardRepository()
    run = await bb.create_run(project_id="conflict_test", budget_usd=5.0)
    res_mgr = WriteReservationManager(bb)

    t1 = await bb.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="alpha",
        title="Task 1",
        description="T1",
        write_set=["backend/app/features/alpha/"],
        base_sha=base_sha,
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.VERIFIED,
    )
    t2 = await bb.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="alpha",
        title="Task 2",
        description="T2",
        write_set=["backend/app/features/alpha/"],
        base_sha=base_sha,
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.VERIFIED,
    )

    for task, branch, body in [
        (t1, "task/c1", "VAL = 'from_t1'\n"),
        (t2, "task/c2", "VAL = 'from_t2'\n"),
    ]:
        wt = wt_root / task.id
        await git.create_worktree(repo_dir, branch, base_sha, wt)
        (wt / "backend" / "app" / "features" / "alpha" / "shared.py").write_text(
            body, encoding="utf-8"
        )
        await git.commit(wt, f"edit shared ({task.id})", ["backend/app/features/alpha/"])
        await bb.update_task_metadata(task.id, task.version, branch=branch, worktree_path=str(wt))

    # Tier 3 unresolved conflict: no resolver -> abort rebase, discard WIP, increment attempt
    mq_no_resolver = SerialMergeQueue(
        bb,
        git,
        HeavyExecSemaphore(2),
        res_mgr,
        repo_dir=repo_dir,
        worktrees_root=wt_root,
        workspaces_root=ws_root,
    )
    assert (await mq_no_resolver.process_task(run.id, t1.id)).status == "merged"

    # Unapproved pending_deps raises cleanly or escalates
    out_t2 = await mq_no_resolver.process_task(run.id, t2.id)
    assert out_t2.status == "requeued_rerun"
    assert out_t2.resolution_tier == "rerun"
    t2_requeued = await bb.get_task(t2.id)
    assert t2_requeued.status == TaskStatus.READY and t2_requeued.attempt == 1

    # Tier 2: Integration Agent resolver resolves conflicting rebase cleanly
    t3 = await bb.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="alpha",
        title="Task 3",
        description="T3",
        write_set=["backend/app/features/alpha/"],
        base_sha=base_sha,
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.VERIFIED,
    )
    wt3 = wt_root / t3.id
    await git.create_worktree(repo_dir, "task/c3", base_sha, wt3)
    (wt3 / "backend" / "app" / "features" / "alpha" / "shared.py").write_text(
        "VAL = 'from_t3'\n", encoding="utf-8"
    )
    await git.commit(wt3, "edit shared (t3)", ["backend/app/features/alpha/"])
    await bb.update_task_metadata(t3.id, t3.version, branch="task/c3", worktree_path=str(wt3))

    class _IntegrationResolver:
        async def resolve_conflicts(
            self,
            *,
            worktree_dir: Path,
            task: TaskRecord,
            conflicted_files: Sequence[str],
            main_sha: str,
        ) -> bool:
            _ = (task, conflicted_files, main_sha)
            (worktree_dir / "backend" / "app" / "features" / "alpha" / "shared.py").write_text(
                "VAL = 'merged_t1_and_t3'\n", encoding="utf-8"
            )
            return True

    mq_tier2 = SerialMergeQueue(
        bb,
        git,
        HeavyExecSemaphore(2),
        res_mgr,
        repo_dir=repo_dir,
        worktrees_root=wt_root,
        workspaces_root=ws_root,
        conflict_resolver=_IntegrationResolver(),
    )
    out_t3 = await mq_tier2.process_task(run.id, t3.id)
    assert out_t3.status == "merged"
    assert out_t3.resolution_tier == "integration_agent"
    assert (repo_dir / "backend" / "app" / "features" / "alpha" / "shared.py").read_text(
        encoding="utf-8"
    ) == "VAL = 'merged_t1_and_t3'\n"
    _ = UnapprovedDependencyError
