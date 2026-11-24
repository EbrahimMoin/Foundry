"""Unit tests for ParallelWaveScheduler, ImplementerGraph, A8-2 volumes & A8-3 G2 semaphore."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from foundry.agents.graphs import (
    ImplementerGraphDeps,
    ImplementerState,
    ImplementerTaskResult,
    build_implementer_graph,
    implementer_thread_id,
)
from foundry.blackboard import (
    EventDispatcher,
    EventRelay,
    InMemoryBlackboardRepository,
    TaskRecord,
    TaskStatus,
)
from foundry.concurrency import SerialMergeQueue, WriteReservationManager
from foundry.config import RuntimeConfig
from foundry.config.schemas import ProviderConfig
from foundry.gateway import GatewayRequest, GatewayResponse, ModelGateway, TransportHealth
from foundry.gateway.protocol import ToolCallSpec
from foundry.gateway.router import RunBudgetState
from foundry.mcp_servers import GitMCPServer, SandboxMCPServer
from foundry.sandbox import (
    HeavyExecSemaphore,
    LocalDockerProvider,
    PackageInstallPolicy,
    sync_worktree_to_workspace,
)
from foundry.sandbox.protocol import ExecPriority, ExecResult, Sandbox
from foundry.security import HardenedGit
from foundry.stack_packs import FastAPIReactPostgresPack
from foundry.supervisor import ParallelWaveScheduler
from foundry.verification import GateRunner


class _FakeOfflineProvider:
    async def create(
        self, task_id: str, image: str, workspace: Path, env: dict[str, str]
    ) -> Sandbox:
        return Sandbox(
            task_id=task_id, container_id=f"sbx_{task_id}", workspace_dir=workspace, image=image
        )

    async def exec(
        self,
        sandbox: Sandbox,
        cmd: list[str],
        *,
        network: bool = False,
        heavy: bool = False,
        priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
        timeout_s: int = 600,
    ) -> ExecResult:
        _ = (network, heavy, priority, timeout_s)
        if "-c" in cmd and any(".foundry_tmp/openapi.json" in part for part in cmd):
            out_file = sandbox.workspace_dir / ".foundry_tmp" / "openapi.json"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(
                json.dumps(
                    {
                        "openapi": "3.1.0",
                        "info": {"title": "App", "version": "1.0.0"},
                        "paths": {},
                    }
                ),
                encoding="utf-8",
            )
        return ExecResult(exit_code=0, stdout="ok", stderr="")

    async def destroy(self, sandbox: Sandbox) -> None:
        _ = sandbox


class _ParallelScriptedTransport:
    name = "parallel_scripted"

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        slug = request.task_slug or "app"
        step = self.calls.get(slug, 0)
        self.calls[slug] = step + 1
        feature = "bookmarks" if "bookmarks" in slug else "tags"
        if step == 0:
            tc = ToolCallSpec(
                id=f"call_{slug}_0",
                name="write_file",
                args={
                    "path": f"backend/app/features/{feature}/router.py",
                    "content": f"FEATURE = '{feature}'\n",
                },
            )
        elif step == 1:
            tc = ToolCallSpec(
                id=f"call_{slug}_1",
                name="run_gate_g1",
                args={},
            )
        else:
            tc = ToolCallSpec(
                id=f"call_{slug}_2",
                name="finish_build",
                args={"summary": f"Built {feature} cleanly"},
            )
        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=provider_cfg.transport,
            billing_mode=provider_cfg.billing_mode,
            content="",
            tool_calls=[tc],
            tokens_in=50,
            tokens_out=20,
        )

    async def health_check(self, provider_id: str, provider_cfg: ProviderConfig) -> TransportHealth:
        return TransportHealth(
            provider_id=provider_id,
            transport=provider_cfg.transport,
            healthy=True,
            detail="scripted",
        )


@pytest.mark.asyncio
async def test_parallel_scheduler_runs_two_implementers_and_merges_serially(
    tmp_path: Path,
) -> None:
    git = HardenedGit()
    repo_dir = tmp_path / "repo"
    await git.init_repo(repo_dir)
    for feat in ("bookmarks", "tags"):
        d = repo_dir / "backend" / "app" / "features" / feat
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("", encoding="utf-8")
    base_sha = await git.commit(repo_dir, "wave1 base", ["backend/"], allow_infra=True)

    wt_root = tmp_path / "worktrees"
    ws_root = tmp_path / "workspaces"
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="parallel_run", budget_usd=5.0)
    res_mgr = WriteReservationManager(repo)
    sem = HeavyExecSemaphore(2)
    relay = EventRelay(repo, EventDispatcher(repo))
    mq = SerialMergeQueue(
        repo,
        git,
        sem,
        res_mgr,
        repo_dir=repo_dir,
        worktrees_root=wt_root,
        workspaces_root=ws_root,
    )

    t1 = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="bookmarks",
        title="Bookmarks API",
        description="Build bookmarks router",
        write_set=["backend/app/features/bookmarks/"],
        base_sha=base_sha,
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )
    t2 = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="tags",
        title="Tags API",
        description="Build tags router",
        write_set=["backend/app/features/tags/"],
        base_sha=base_sha,
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )

    from foundry.config import BudgetConfig, default_models_config

    models_cfg = default_models_config()
    transport = _ParallelScriptedTransport()
    t_kind = models_cfg.providers["gemini_paid"].transport
    gateway = ModelGateway(
        models_cfg,
        transports={t_kind: transport},
        run_budget=RunBudgetState(BudgetConfig()),
    )
    provider = _FakeOfflineProvider()
    stack_pack = FastAPIReactPostgresPack()

    observed_threads: list[str] = []

    async def _worker(task: TaskRecord, context: dict[str, Any]) -> ImplementerTaskResult:
        wt_dir = wt_root / task.id
        ws_dir = ws_root / task.id
        branch = task.branch or f"task/{task.id}"
        await git.create_worktree(repo_dir, branch, base_sha, wt_dir)
        sync_worktree_to_workspace(wt_dir, ws_dir)
        await repo.update_task_metadata(
            task.id, task.version, branch=branch, worktree_path=str(wt_dir)
        )

        write_set = list(context["write_set"])
        sbx = await provider.create(task.id, "mock:latest", ws_dir, {})
        sbx_mcp = SandboxMCPServer(
            provider,
            sbx,
            write_set=write_set,
            package_policy=PackageInstallPolicy(),
        )
        git_mcp = GitMCPServer(
            git,
            worktree_dir=wt_dir,
            workspace_dir=ws_dir,
            write_set=write_set,
        )
        runner = GateRunner(
            provider,
            sbx,
            stack_pack=stack_pack,
            git=git,
            worktree_dir=wt_dir,
            write_set=write_set,
            run_schemathesis=False,
            semaphore=sem,
        )
        deps = ImplementerGraphDeps(
            gateway=gateway,
            sandbox_mcp=sbx_mcp,
            git_mcp=git_mcp,
            gate_runner=runner,
            workspace_dir=ws_dir,
            worktree_dir=wt_dir,
            git=git,
            repo=repo,
            task=task,
            write_set=write_set,
            contracts=context["contracts"],
            boundary_schema_ref=context["boundary_schema_ref"],
        )
        graph = build_implementer_graph(deps)
        tid = implementer_thread_id(run.id, task.id, task.attempt + 1)
        observed_threads.append(tid)
        init_state: ImplementerState = {
            "run_id": run.id,
            "task_id": task.id,
            "task_slug": f"feat_{task.feature}",
            "attempt": task.attempt + 1,
            "max_attempts": task.max_attempts,
            "step_index": 0,
            "max_steps": 12,
        }
        out = await graph.ainvoke(init_state, {"configurable": {"thread_id": tid}})
        return ImplementerTaskResult(
            task_id=task.id,
            status=out["final_status"],
            committed_sha=out.get("committed_sha"),
            error=out.get("error"),
        )

    scheduler = ParallelWaveScheduler(
        repo,
        res_mgr,
        mq,
        relay,
        worker_fn=_worker,
        max_concurrent_tasks=4,
        heartbeat_interval_s=0.05,
    )
    outcomes = await scheduler.run_wave(run.id, wave=2, contracts={"api": {"paths": {}}})
    await gateway.aclose()

    assert len(outcomes) == 2
    assert all(o.status == "merged" for o in outcomes)
    assert len(set(observed_threads)) == 2
    assert (repo_dir / "backend" / "app" / "features" / "bookmarks" / "router.py").exists()
    assert (repo_dir / "backend" / "app" / "features" / "tags" / "router.py").exists()
    assert await res_mgr.list_active(run.id) == []
    events = await repo.list_events_after(0, run_id=run.id)
    assert any(e.type == "wave.completed" for e in events)
    _ = t1, t2


@pytest.mark.asyncio
async def test_a8_container_volumes_and_g2_host_scanner_semaphore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. A8-2: Verify LocalDockerProvider mounts container volumes and deletes them
    calls: dict[str, Any] = {"run": None, "removed_vols": []}

    class _FakeContainer:
        id = "cid_vol_test"

        def remove(self, force: bool = False) -> None:
            calls["removed"] = force

    class _FakeVol:
        def __init__(self, name: str) -> None:
            self.name = name

        def remove(self, force: bool = False) -> None:
            calls["removed_vols"].append((self.name, force))

    fake_client = SimpleNamespace(
        containers=SimpleNamespace(
            run=lambda **kw: (calls.__setitem__("run", kw), _FakeContainer())[1],
            get=lambda _cid: _FakeContainer(),
        ),
        volumes=SimpleNamespace(get=lambda name: _FakeVol(name)),
    )
    ws = tmp_path / "ws"
    (ws / "frontend").mkdir(parents=True)
    provider = LocalDockerProvider(
        RuntimeConfig(), HeavyExecSemaphore(2), client_factory=lambda: fake_client
    )
    sbx = await provider.create("t_vol", "foundry-sandbox:sha256", ws, {})
    vols = calls["run"]["volumes"]
    assert vols["foundry-venv-t_vol"] == {"bind": "/opt/venv", "mode": "rw"}
    assert vols["foundry-nm-t_vol"] == {"bind": "/workspace/frontend/node_modules", "mode": "rw"}
    assert (ws / "frontend" / "node_modules").is_dir()

    await provider.destroy(sbx)
    assert sorted(v[0] for v in calls["removed_vols"]) == [
        "foundry-nm-t_vol",
        "foundry-venv-t_vol",
    ]

    # 2. A8-3: Verify GateRunner.run_g2 holds 1 HeavyExecSemaphore slot during host scanners
    sem = HeavyExecSemaphore(max_slots=1)
    active_during_scan: list[int] = []

    def _fake_scanners(_target: Path) -> list[Any]:
        active_during_scan.append(sem.active_slots)
        return []

    monkeypatch.setattr("foundry.verification.gates.run_optional_host_scanners", _fake_scanners)
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    runner = GateRunner(
        _FakeOfflineProvider(),
        Sandbox(task_id="t_sem", container_id="cid", workspace_dir=ws2, image="mock:latest"),
        stack_pack=FastAPIReactPostgresPack(),
        write_set=[],
        run_schemathesis=False,
        semaphore=sem,
    )
    report = await runner.run_g2()
    assert report.passed is True
    assert active_during_scan == [1]
    assert sem.active_slots == 0


@pytest.mark.asyncio
async def test_scheduler_handles_schema_rebase_restart_and_clean_retry() -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="schema_restart", budget_usd=5.0)
    res_mgr = WriteReservationManager(repo)
    relay = EventRelay(repo, EventDispatcher(repo))

    task = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="items",
        title="Items API",
        description="Needs clean restart on schema conflict",
        write_set=["backend/app/features/items/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )

    attempts_seen: list[int] = []

    class _FakeMQ(SerialMergeQueue):
        async def process_task(self, run_id: str, task_id: str) -> Any:
            t = await self._repo.get_task(task_id)
            merged = await self._repo.set_task_status(task_id, TaskStatus.MERGED, t.version)
            await self._reservations.release(run_id, task_id, reason="merged")
            return SimpleNamespace(
                task_id=merged.id,
                status="merged",
                merged_sha="deadbeef",
                resolution_tier="clean",
                spawned_integration_task_id=None,
                detail="ok",
            )

    async def _worker(t: TaskRecord, _ctx: dict[str, Any]) -> ImplementerTaskResult:
        attempts_seen.append(t.attempt)
        if t.attempt == 0:
            return ImplementerTaskResult(
                task_id=t.id,
                status="schema_rebase_restart",
                new_base_sha="new_schema_sha",
                error="conflict on rebase",
            )
        return ImplementerTaskResult(task_id=t.id, status="verified_g2", committed_sha="abc123")

    scheduler = ParallelWaveScheduler(
        repo,
        res_mgr,
        _FakeMQ(
            repo,
            HardenedGit(),
            HeavyExecSemaphore(2),
            res_mgr,
            repo_dir=Path("."),
            worktrees_root=Path("."),
            workspaces_root=Path("."),
        ),
        relay,
        worker_fn=_worker,
        heartbeat_interval_s=0.05,
    )
    outcomes = await scheduler.run_wave(run.id, wave=2, contracts={"api": {"paths": {}}})
    assert len(outcomes) == 1 and outcomes[0].status == "merged"
    assert attempts_seen == [0, 1]
    assert (await repo.get_task(task.id)).base_sha == "new_schema_sha"
    _ = asyncio.sleep
