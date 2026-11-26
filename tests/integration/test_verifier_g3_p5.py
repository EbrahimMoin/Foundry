"""Integration tests for Cross-Family G3 Verifier worker, Test-Validator loop & scheduler hooks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from foundry.agents.graphs.verifier_graph import VerifierGraphDeps, run_g3_verification
from foundry.blackboard.events import EventDispatcher, EventRelay
from foundry.blackboard.models import TaskStatus
from foundry.blackboard.repository import InMemoryBlackboardRepository
from foundry.concurrency import SerialMergeQueue, WriteReservationManager
from foundry.config.loader import default_models_config
from foundry.config.schemas import ProviderConfig, SandboxLimitsConfig
from foundry.gateway.cassette import CassetteStore, MissPolicy
from foundry.gateway.protocol import GatewayRequest, GatewayResponse, ToolCallSpec
from foundry.gateway.router import ModelGateway
from foundry.sandbox.protocol import ExecPriority, ExecResult, Sandbox
from foundry.sandbox.semaphore import HeavyExecSemaphore
from foundry.security.hardened_git import HardenedGit
from foundry.security.vault import CredentialVault
from foundry.supervisor.scheduler import ParallelWaveScheduler
from foundry.verification import G4ReviewResult, RiskAssessment


class _ScriptedVerifierTransport:
    name = "openai_compat"

    def __init__(self, script: list[tuple[str, list[ToolCallSpec]]]) -> None:
        self._script = list(script)
        self.requests: list[GatewayRequest] = []

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        self.requests.append(request)
        content, calls = self._script.pop(0)
        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=provider_cfg.transport,
            billing_mode=provider_cfg.billing_mode,
            content=content,
            tool_calls=calls,
            tokens_in=150,
            tokens_out=60,
        )

    async def health_check(self, provider_id: str, provider_cfg: ProviderConfig) -> Any:
        raise NotImplementedError


class _FakeG3SandboxProvider:
    def __init__(self, semaphore: HeavyExecSemaphore) -> None:
        self.semaphore = semaphore
        self.exec_priorities: list[ExecPriority] = []
        self.run_count = 0

    async def create(
        self,
        workspace_dir: Path,
        *,
        db_url: str | None = None,
        limits: SandboxLimitsConfig | None = None,
    ) -> Sandbox:
        _ = (db_url, limits)
        return Sandbox(
            task_id="tsk_be",
            container_id="sbx_g3_test",
            workspace_dir=workspace_dir,
            image="foundry-sandbox:test",
        )

    async def exec(
        self,
        sandbox: Sandbox,
        cmd: list[str],
        *,
        network: bool = False,
        env: dict[str, str] | None = None,
        timeout_s: int = 300,
        heavy: bool = False,
        priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
    ) -> ExecResult:
        _ = (cmd, network, env, timeout_s)
        self.exec_priorities.append(priority)

        async def _run() -> ExecResult:
            self.run_count += 1
            trace_file = sandbox.workspace_dir / ".foundry_tmp" / "g3_request_trace.jsonl"
            trace_file.parent.mkdir(parents=True, exist_ok=True)
            if self.run_count == 1:
                # Simulate outbound 422 schema error on first run -> invalid_test_request
                trace_file.write_text(
                    json.dumps(
                        {
                            "test_nodeid": "test_bookmarks_blackbox.py::test_create",
                            "method": "POST",
                            "path": "/api/bookmarks",
                            "status_code": 422,
                            "request_valid": False,
                            "expect_invalid": False,
                            "validation_error": "Missing required body field 'url'",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return ExecResult(
                    exit_code=1,
                    stdout="FAILED test_create - assert 422 == 201",
                    stderr="",
                    duration_ms=10,
                )
            trace_file.write_text(
                json.dumps(
                    {
                        "test_nodeid": "test_bookmarks_blackbox.py::test_create",
                        "method": "POST",
                        "path": "/api/bookmarks",
                        "status_code": 201,
                        "request_valid": True,
                        "expect_invalid": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return ExecResult(exit_code=0, stdout="1 passed in 0.1s", stderr="", duration_ms=10)

        if heavy:
            async with self.semaphore.acquire(priority):
                return await _run()
        return await _run()

    async def destroy(self, sandbox: Sandbox) -> None:
        _ = sandbox


@pytest.mark.asyncio
async def test_g3_self_fix_commit_and_semaphore_priority(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    wt_root = tmp_path / "worktrees"
    ws_dir = tmp_path / "workspaces" / "tsk_be"
    ws_dir.mkdir(parents=True, exist_ok=True)

    git = HardenedGit()
    await git.init_repo(repo_dir)
    (repo_dir / "README.md").write_text("base\n", encoding="utf-8")
    await git.commit(repo_dir, "init", ["README.md"], allow_infra=True)
    base_sha = await git.head_sha(repo_dir, "main")

    wt_dir = wt_root / "tsk_be"
    await git.create_worktree(repo_dir, "task/tsk_be-a1", base_sha, wt_dir)
    (wt_dir / "backend" / "app" / "features" / "bookmarks").mkdir(parents=True, exist_ok=True)
    (wt_dir / "backend" / "app" / "features" / "bookmarks" / "router.py").write_text(
        "# feature route\n", encoding="utf-8"
    )
    await git.commit(wt_dir, "feat(bookmarks): route", ["backend/app/features/bookmarks/"])

    bb = InMemoryBlackboardRepository()
    run = await bb.create_run(project_id="proj", budget_usd=5.0, phase="building")
    task = await bb.create_task(
        task_id="tsk_be",
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="bookmarks",
        kind="build",
        title="Bookmarks API",
        description="Implement /api/bookmarks",
        write_set=["backend/app/features/bookmarks/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.IN_REVIEW,
    )

    cfg = default_models_config()
    transport = _ScriptedVerifierTransport(
        [
            (
                "Writing initial black-box test.",
                [
                    ToolCallSpec(
                        id="c1",
                        name="write_test_file",
                        args={
                            "path": "backend/tests/blackbox/bookmarks/test_api.py",
                            "content": "def test_create(): pass\n",
                        },
                    ),
                    ToolCallSpec(id="c2", name="run_g3_tests", args={}),
                ],
            ),
            (
                "Fixing missing url field flagged by post-test Test-Validator.",
                [
                    ToolCallSpec(
                        id="c3",
                        name="write_test_file",
                        args={
                            "path": "backend/tests/blackbox/bookmarks/test_api.py",
                            "content": "def test_create():\n    payload = {'url': 'https://a.test'}\n",
                        },
                    ),
                    ToolCallSpec(id="c4", name="run_g3_tests", args={}),
                ],
            ),
        ]
    )
    gw = ModelGateway(
        cfg,
        vault=CredentialVault(tmp_path / "vault.enc", passphrase="test"),
        cassettes=CassetteStore(tmp_path / "cas", miss_policy=MissPolicy.PASSTHROUGH),
        transports={"openai_compat": transport, "google_genai": transport},
    )
    sem = HeavyExecSemaphore(max_slots=2)
    provider = _FakeG3SandboxProvider(sem)
    sbx = Sandbox(
        task_id=task.id,
        container_id="sbx_g3",
        workspace_dir=ws_dir,
        image="foundry-sandbox:test",
    )

    result = await run_g3_verification(
        VerifierGraphDeps(
            gateway=gw,
            provider=provider,
            sandbox=sbx,
            repo=bb,
            task=task,
            allowed_test_paths=["backend/tests/blackbox/bookmarks/"],
            task_write_set=["backend/app/features/bookmarks/"],
            contracts={"api": {"openapi": "3.1.0", "paths": {"/api/bookmarks": {"post": {}}}}},
            worktree_dir=wt_dir,
            git=git,
            peer_implementer_family="gemini",
        )
    )

    assert result.status == "verified"
    assert result.self_fix_attempts == 1
    assert result.committed_test_sha is not None
    assert provider.exec_priorities == [ExecPriority.VERIFY_G3, ExecPriority.VERIFY_G3]

    # Passing G3 test committed cleanly onto the task worktree branch
    assert (wt_dir / "backend/tests/blackbox/bookmarks/test_api.py").is_file()
    feat_rec = await bb.get_feature(run.id, "bookmarks")
    assert feat_rec is not None
    assert feat_rec.status == "verified"


@pytest.mark.asyncio
async def test_g3_self_fix_cap_escalates_after_two_failures(tmp_path: Path) -> None:
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    bb = InMemoryBlackboardRepository()
    run = await bb.create_run(project_id="proj", budget_usd=5.0, phase="building")
    task = await bb.create_task(
        task_id="tsk_cap",
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="tags",
        kind="build",
        title="Tags API",
        description="Tags",
        write_set=["backend/app/features/tags/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.IN_REVIEW,
    )

    class _AlwaysBrokenProvider(_FakeG3SandboxProvider):
        async def exec(
            self,
            sandbox: Sandbox,
            cmd: list[str],
            *,
            network: bool = False,
            env: dict[str, str] | None = None,
            timeout_s: int = 300,
            heavy: bool = False,
            priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
        ) -> ExecResult:
            _ = (sandbox, cmd, network, env, timeout_s, heavy)
            self.exec_priorities.append(priority)
            return ExecResult(
                exit_code=2,
                stdout="ERROR collecting test_tags.py: SyntaxError: invalid syntax",
                stderr="",
            )

    cfg = default_models_config()
    transport = _ScriptedVerifierTransport(
        [
            ("step1", [ToolCallSpec(id="t1", name="run_g3_tests", args={})]),
            ("step2", [ToolCallSpec(id="t2", name="run_g3_tests", args={})]),
            ("step3", [ToolCallSpec(id="t3", name="run_g3_tests", args={})]),
        ]
    )
    gw = ModelGateway(
        cfg,
        vault=CredentialVault(tmp_path / "vault.enc", passphrase="test"),
        cassettes=CassetteStore(tmp_path / "cas", miss_policy=MissPolicy.PASSTHROUGH),
        transports={"openai_compat": transport, "google_genai": transport},
    )
    sem = HeavyExecSemaphore(max_slots=2)
    res = await run_g3_verification(
        VerifierGraphDeps(
            gateway=gw,
            provider=_AlwaysBrokenProvider(sem),
            sandbox=Sandbox(
                task_id=task.id,
                container_id="sbx_cap",
                workspace_dir=ws_dir,
                image="foundry-sandbox:test",
            ),
            repo=bb,
            task=task,
            allowed_test_paths=["backend/tests/blackbox/tags/"],
            contracts={},
            peer_implementer_family="gemini",
            max_self_fix_attempts=2,
        )
    )
    assert res.status == "escalated_orchestrator"
    assert res.self_fix_attempts == 3


@pytest.mark.asyncio
async def test_scheduler_routes_g3_code_wrong_back_to_ready(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    git = HardenedGit()
    await git.init_repo(repo_dir)
    (repo_dir / "README.md").write_text("base\n", encoding="utf-8")
    await git.commit(repo_dir, "init", ["README.md"], allow_infra=True)

    bb = InMemoryBlackboardRepository()
    disp = EventDispatcher(bb)
    relay = EventRelay(bb, disp)
    run = await bb.create_run(project_id="proj", budget_usd=5.0, phase="building")
    t1 = await bb.create_task(
        task_id="tsk_s1",
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="auth",
        kind="build",
        title="Auth route",
        description="Auth",
        write_set=["backend/app/features/auth/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )

    reservations = WriteReservationManager(bb)
    merge_q = SerialMergeQueue(
        bb,
        git,
        HeavyExecSemaphore(2),
        reservations,
        repo_dir=repo_dir,
        worktrees_root=tmp_path / "wt",
        workspaces_root=tmp_path / "ws",
    )

    calls = 0

    async def _worker(task: Any, ctx: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        _ = (task, ctx)
        if calls == 1:
            return {"status": "verified_g2"}
        # Second attempt aborted cleanly as failed after G3 tested code_wrong routing
        return {"status": "failed", "error": "stop after requeue verification"}

    async def _g3_hook(task: Any, ctx: dict[str, Any]) -> Any:
        _ = ctx
        from foundry.agents.graphs.verifier_graph import G3VerificationResult

        return G3VerificationResult(
            task_id=task.id,
            feature=task.feature or "auth",
            status="code_wrong",
            summary="Server returned 500 on POST /api/auth/login",
        )

    async def _g4_hook(task: Any, ctx: dict[str, Any]) -> G4ReviewResult:
        _ = (task, ctx)
        return G4ReviewResult(
            approved=True,
            skipped=False,
            risk=RiskAssessment(should_run=True, reasons=["auth"]),
        )

    sched = ParallelWaveScheduler(
        bb,
        reservations,
        merge_q,
        relay,
        worker_fn=_worker,
        g3_verifier=_g3_hook,
        g4_reviewer=_g4_hook,
        heartbeat_interval_s=30.0,
    )

    # Verify attempt 0 -> code_wrong -> attempt 1 -> failed -> ESCALATED cleanly ends wave
    t_upd = await bb.get_task(t1.id)
    assert t_upd.max_attempts == 3
    await asyncio.wait_for(sched.run_wave(run.id, wave=2), timeout=5.0)
    final_task = await bb.get_task(t1.id)
    assert calls >= 2
    assert final_task.status in {TaskStatus.ESCALATED, TaskStatus.READY}


@pytest.mark.asyncio
async def test_g3_blocks_same_family_and_out_of_scope_test_paths(tmp_path: Path) -> None:
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    bb = InMemoryBlackboardRepository()
    run = await bb.create_run(project_id="proj", budget_usd=5.0, phase="building")
    task = await bb.create_task(
        task_id="tsk_scope",
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="notes",
        kind="build",
        title="Notes API",
        description="Notes",
        write_set=["backend/app/features/notes/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.IN_REVIEW,
    )
    cfg = default_models_config()
    sem = HeavyExecSemaphore(max_slots=2)
    sbx = Sandbox(
        task_id=task.id,
        container_id="sbx_scope",
        workspace_dir=ws_dir,
        image="foundry-sandbox:test",
    )

    # 1. Same model family ("xai" matching @verifier) triggers GatewayPolicyError escalation
    transport_same = _ScriptedVerifierTransport([])
    gw_same = ModelGateway(
        cfg,
        vault=CredentialVault(tmp_path / "vault.enc", passphrase="test"),
        cassettes=CassetteStore(tmp_path / "cas", miss_policy=MissPolicy.PASSTHROUGH),
        transports={"openai_compat": transport_same, "google_genai": transport_same},
    )
    same_res = await run_g3_verification(
        VerifierGraphDeps(
            gateway=gw_same,
            provider=_FakeG3SandboxProvider(sem),
            sandbox=sbx,
            repo=bb,
            task=task,
            allowed_test_paths=["backend/tests/blackbox/notes/"],
            contracts={},
            peer_implementer_family="xai",
        )
    )
    assert same_res.status == "escalated_orchestrator"
    assert "cross-family diversity" in same_res.summary.lower()

    # 2. Attempting to write outside allowed_test_paths is rejected without modifying disk
    transport_oob = _ScriptedVerifierTransport(
        [
            (
                "Attempting forbidden write",
                [
                    ToolCallSpec(
                        id="b1",
                        name="write_test_file",
                        args={
                            "path": "backend/app/features/notes/router.py",
                            "content": "# hacked\n",
                        },
                    ),
                    ToolCallSpec(id="b2", name="run_g3_tests", args={}),
                ],
            ),
            (
                "Writing valid black-box test after rejection",
                [
                    ToolCallSpec(
                        id="b3",
                        name="write_test_file",
                        args={
                            "path": "backend/tests/blackbox/notes/test_notes.py",
                            "content": "def test_ok(): pass\n",
                        },
                    ),
                    ToolCallSpec(id="b4", name="run_g3_tests", args={}),
                ],
            ),
        ]
    )
    gw_oob = ModelGateway(
        cfg,
        vault=CredentialVault(tmp_path / "vault2.enc", passphrase="test"),
        cassettes=CassetteStore(tmp_path / "cas2", miss_policy=MissPolicy.PASSTHROUGH),
        transports={"openai_compat": transport_oob, "google_genai": transport_oob},
    )
    prov = _FakeG3SandboxProvider(sem)
    oob_res = await run_g3_verification(
        VerifierGraphDeps(
            gateway=gw_oob,
            provider=prov,
            sandbox=sbx,
            repo=bb,
            task=task,
            allowed_test_paths=["backend/tests/blackbox/notes/"],
            contracts={},
            peer_implementer_family="gemini",
        )
    )
    assert oob_res.status == "verified"
    assert not (ws_dir / "backend/app/features/notes/router.py").exists()
    assert (ws_dir / "backend/tests/blackbox/notes/test_notes.py").is_file()
