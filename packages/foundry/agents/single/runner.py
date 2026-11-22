"""SingleAgentRunner (v0.1) & host-safe ReplayMockSandboxProvider for offline testing."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from foundry.agents.single.graph import SingleAgentDeps, SingleAgentState, build_single_agent_graph
from foundry.agents.single.prompts import SINGLE_AGENT_SYSTEM_PROMPT, build_initial_user_prompt
from foundry.config.loader import default_foundry_config, default_models_config
from foundry.config.schemas import BudgetConfig, DataPolicyConfig, ModelsConfig, RuntimeConfig
from foundry.gateway.cassette import CassetteStore
from foundry.gateway.router import ModelGateway, RunBudgetState
from foundry.mcp_servers.git_server import GitMCPServer
from foundry.mcp_servers.sandbox_server import SandboxMCPServer
from foundry.sandbox.docker_provider import LocalDockerProvider
from foundry.sandbox.protocol import ExecPriority, ExecResult, Sandbox, SandboxProvider
from foundry.sandbox.semaphore import HeavyExecSemaphore
from foundry.sandbox.workspace_sync import sync_worktree_to_workspace
from foundry.security.hardened_git import HardenedGit
from foundry.security.vault import CredentialVault
from foundry.security.workspace_guard import validate_workspace_tree
from foundry.stack_packs import FastAPIReactPostgresPack, StackPack
from foundry.verification.gates import GateRunner
from foundry.verification.models import GateReport


class BuildResult(BaseModel):
    """Structured result emitted by a SingleAgentRunner build."""

    status: str
    app_name: str
    task_slug: str
    repo_dir: Path
    worktree_dir: Path
    workspace_dir: Path
    g2_passed: bool = False
    committed_sha: str | None = None
    steps: int = 0
    attempts: int = 1
    llm_calls: int = 0
    cost_usd: float = 0.0
    est_value_usd: float = 0.0
    tokens_in: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0
    wall_clock_s: float = 0.0
    model_ids: list[str] = Field(default_factory=list)
    gate_report: GateReport | None = None
    error: str | None = None


class ReplayMockSandboxProvider:
    """Offline SandboxProvider for `--replay --sandbox replay-mock` without host code exec."""

    def __init__(
        self,
        *,
        failing_commands: set[str] | None = None,
        fail_once_commands: set[str] | None = None,
        openapi_override: dict[str, Any] | None = None,
    ) -> None:
        self._failing_commands = set(failing_commands or ())
        self._fail_once_commands = set(fail_once_commands or ())
        self._openapi_override = openapi_override
        self.exec_history: list[list[str]] = []

    async def create(
        self,
        task_id: str,
        image: str,
        workspace: Path,
        env: dict[str, str],
    ) -> Sandbox:
        await asyncio.to_thread(validate_workspace_tree, workspace, ignore_git_dir=False)
        return Sandbox(
            task_id=task_id,
            container_id=f"replay-mock-{task_id}",
            workspace_dir=workspace,
            image=image,
            database_url=env.get("DATABASE_URL"),
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
        self.exec_history.append(list(cmd))
        joined = " ".join(cmd)
        binary = Path(cmd[0]).name if cmd else ""
        key = "schemathesis" if "schemathesis" in joined else binary

        if key in self._fail_once_commands:
            self._fail_once_commands.remove(key)
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr=f"Simulated one-time failure for {key}",
                duration_ms=1,
            )
        if key in self._failing_commands:
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr=f"Simulated failure for {key}",
                duration_ms=1,
            )

        if ".foundry_tmp/openapi.json" in joined:
            await asyncio.to_thread(self._write_mock_openapi_json, sandbox.workspace_dir)
            return ExecResult(exit_code=0, stdout="exported mock openapi.json", duration_ms=1)

        return ExecResult(exit_code=0, stdout=f"replay-mock passed: {key}", duration_ms=1)

    def _write_mock_openapi_json(self, workspace_dir: Path) -> None:
        validate_workspace_tree(workspace_dir, ignore_git_dir=False)
        out_path = workspace_dir / ".foundry_tmp" / "openapi.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self._openapi_override is not None:
            doc = self._openapi_override
        else:
            contract_path = workspace_dir / "contracts" / "openapi.yaml"
            raw = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
            doc = raw if isinstance(raw, dict) else {"openapi": "3.1.0", "paths": {}}
        out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    async def destroy(self, sandbox: Sandbox) -> None:
        _ = sandbox


def _load_pinned_openapi(path: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    if path is None:
        return None, None
    validate_workspace_tree(path, ignore_git_dir=False)
    raw_text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw_text)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected OpenAPI mapping in {path}")
    return loaded, raw_text


def _load_pinned_testids(path: Path | None) -> tuple[list[str], str | None]:
    if path is None:
        return [], None
    validate_workspace_tree(path, ignore_git_dir=False)
    raw_text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw_text)
    if isinstance(loaded, list):
        return [str(x) for x in loaded], raw_text
    if isinstance(loaded, dict) and isinstance(loaded.get("required_testids"), list):
        return [str(x) for x in loaded["required_testids"]], raw_text
    raise ValueError(f"Expected list or 'required_testids' key in {path}")


class SingleAgentRunner:
    """Orchestrates Wave 0 setup, sandbox lifecycle, LangGraph loop, G1/G2, and commit."""

    def __init__(
        self,
        *,
        models_cfg: ModelsConfig | None = None,
        runtime_cfg: RuntimeConfig | None = None,
        budget_cfg: BudgetConfig | None = None,
        data_policy: DataPolicyConfig | None = None,
        stack_pack: StackPack | None = None,
        gateway: ModelGateway | None = None,
        vault: CredentialVault | None = None,
        cassettes: CassetteStore | None = None,
        sandbox_provider: SandboxProvider | None = None,
        git: HardenedGit | None = None,
    ) -> None:
        self.models_cfg = models_cfg or default_models_config()
        self.runtime_cfg = runtime_cfg or default_foundry_config().runtime
        self.budget_cfg = budget_cfg or BudgetConfig()
        self.data_policy = data_policy or DataPolicyConfig()
        self.stack_pack: StackPack = stack_pack or FastAPIReactPostgresPack()
        self._vault = vault or CredentialVault()
        self._cassettes = cassettes
        self._explicit_gateway = gateway
        self.sandbox_provider: SandboxProvider = sandbox_provider or LocalDockerProvider(
            self.runtime_cfg,
            HeavyExecSemaphore(self.runtime_cfg.max_heavy_exec_slots),
        )
        self.git = git or HardenedGit()

    async def run(
        self,
        *,
        spec: str,
        target_dir: Path,
        app_name: str = "foundry-app",
        task_slug: str = "build",
        pinned_openapi_path: Path | None = None,
        pinned_testids_path: Path | None = None,
        write_set: Sequence[str] | None = None,
        max_steps: int = 25,
        max_attempts: int = 3,
        run_frontend: bool = False,
        run_schemathesis: bool = True,
    ) -> BuildResult:
        started = time.perf_counter()
        resolved_target = await asyncio.to_thread(target_dir.resolve)
        repo_dir = resolved_target / "repo"
        worktree_dir = resolved_target / "worktree"
        workspace_dir = resolved_target / "workspace"

        pinned_openapi_doc, pinned_openapi_yaml = await asyncio.to_thread(
            _load_pinned_openapi, pinned_openapi_path
        )
        pinned_testids, pinned_testids_yaml = await asyncio.to_thread(
            _load_pinned_testids, pinned_testids_path
        )

        # 1. Materialize Wave 0 skeleton & initial commit on daemon
        await self.git.init_repo(repo_dir, initial_branch="main")
        await asyncio.to_thread(self.stack_pack.scaffold, repo_dir, app_name)
        if pinned_openapi_yaml is not None:
            await asyncio.to_thread(
                (repo_dir / "contracts" / "openapi.yaml").write_text,
                pinned_openapi_yaml,
                encoding="utf-8",
            )
        if pinned_testids_yaml is not None:
            await asyncio.to_thread(
                (repo_dir / "contracts" / "components.yaml").write_text,
                pinned_testids_yaml,
                encoding="utf-8",
            )

        await self.git.commit(
            repo_dir,
            "chore: wave0 scaffold",
            ["backend/", "frontend/", "contracts/", ".spectral.yaml", "e2e/", "infra/", ".github/"],
            allow_infra=True,
        )

        branch_name = f"build/{task_slug}-{uuid.uuid4().hex[:6]}"
        await self.git.create_worktree(repo_dir, branch_name, "main", worktree_dir)
        await asyncio.to_thread(sync_worktree_to_workspace, worktree_dir, workspace_dir)

        active_write_set = list(write_set or ["backend/", "frontend/", "contracts/"])
        task_id = f"single_{task_slug}_{uuid.uuid4().hex[:8]}"

        owned_gateway = self._explicit_gateway is None
        if self._explicit_gateway is not None:
            gateway = self._explicit_gateway
            ledger = gateway.run_budget
        else:
            ledger = RunBudgetState(self.budget_cfg)
            gateway = ModelGateway(
                self.models_cfg,
                vault=self._vault,
                data_policy=self.data_policy,
                cassettes=self._cassettes,
                run_budget=ledger,
            )

        start_calls_idx = len(ledger.calls_log) if ledger is not None else 0
        sandbox = await self.sandbox_provider.create(
            task_id=task_id,
            image=self.stack_pack.sandbox_image,
            workspace=workspace_dir,
            env={},
        )

        try:
            sandbox_mcp = SandboxMCPServer(
                self.sandbox_provider,
                sandbox,
                write_set=active_write_set,
                package_policy=self.stack_pack.curated_package_policy(),
            )
            git_mcp = GitMCPServer(
                self.git,
                worktree_dir=worktree_dir,
                workspace_dir=workspace_dir,
                write_set=active_write_set,
            )
            gate_runner = GateRunner(
                self.sandbox_provider,
                sandbox,
                stack_pack=self.stack_pack,
                git=self.git,
                worktree_dir=worktree_dir,
                write_set=active_write_set,
                pinned_openapi=pinned_openapi_doc,
                baseline_openapi_path=repo_dir / "contracts" / "openapi.yaml",
                pinned_testids=pinned_testids,
                run_frontend=run_frontend,
                run_schemathesis=run_schemathesis,
            )

            deps = SingleAgentDeps(
                gateway=gateway,
                sandbox_mcp=sandbox_mcp,
                git_mcp=git_mcp,
                gate_runner=gate_runner,
                workspace_dir=workspace_dir,
                task_id=task_id,
            )
            compiled = build_single_agent_graph(deps)
            user_prompt = build_initial_user_prompt(
                spec,
                app_name=app_name,
                write_set=active_write_set,
                pinned_openapi_yaml=pinned_openapi_yaml,
                pinned_testids_yaml=pinned_testids_yaml,
            )
            init_state: SingleAgentState = {
                "spec": spec,
                "app_name": app_name,
                "task_slug": task_slug,
                "messages": [
                    {"role": "system", "content": SINGLE_AGENT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "step_index": 0,
                "max_steps": max_steps,
                "attempt": 1,
                "max_attempts": max_attempts,
                "last_tool_calls": [],
                "done_requested": False,
                "g1_passed": False,
                "g2_passed": False,
                "last_gate_summary": "",
                "committed_sha": None,
                "error": None,
                "final_status": "running",
            }
            final_state = await compiled.ainvoke(
                init_state,
                config={"recursion_limit": max(50, max_steps * 4)},
            )
        finally:
            await self.sandbox_provider.destroy(sandbox)
            if owned_gateway:
                await gateway.aclose()

        new_calls = ledger.calls_log[start_calls_idx:] if ledger is not None else []
        return BuildResult(
            status=str(final_state["final_status"]),
            app_name=app_name,
            task_slug=task_slug,
            repo_dir=repo_dir,
            worktree_dir=worktree_dir,
            workspace_dir=workspace_dir,
            g2_passed=bool(final_state["g2_passed"]),
            committed_sha=final_state["committed_sha"],
            steps=int(final_state["step_index"]),
            attempts=int(final_state["attempt"]),
            llm_calls=len(new_calls),
            cost_usd=round(sum(c.cost_usd for c in new_calls), 6),
            est_value_usd=round(sum(c.est_value_usd for c in new_calls), 6),
            tokens_in=sum(c.tokens_in for c in new_calls),
            tokens_cached=sum(c.tokens_cached for c in new_calls),
            tokens_out=sum(c.tokens_out for c in new_calls),
            wall_clock_s=round(time.perf_counter() - started, 3),
            model_ids=sorted(deps.model_ids),
            gate_report=deps.last_g2_report,
            error=final_state["error"],
        )
