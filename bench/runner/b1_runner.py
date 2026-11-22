"""Baseline B1 benchmark runner (equal-budget single agent + isolated hidden acceptance tests)."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from foundry.agents.single.runner import (
    BuildResult,
    ReplayMockSandboxProvider,
    SingleAgentRunner,
)
from foundry.config.loader import default_foundry_config, default_models_config
from foundry.config.schemas import BudgetConfig, ModelsConfig, RuntimeConfig
from foundry.gateway.cassette import CassetteStore
from foundry.sandbox.docker_provider import LocalDockerProvider
from foundry.sandbox.protocol import ExecPriority, SandboxProvider
from foundry.sandbox.semaphore import HeavyExecSemaphore
from foundry.sandbox.workspace_sync import sync_worktree_to_workspace
from foundry.security.vault import CredentialVault
from foundry.security.workspace_guard import validate_workspace_tree
from foundry.stack_packs import FastAPIReactPostgresPack, StackPack

_HIDDEN_CONFTEST_PY = """from __future__ import annotations

import json
from pathlib import Path

_OUTCOMES: dict[str, str] = {}


def pytest_runtest_logreport(report):
    if report.when == "call":
        _OUTCOMES[report.nodeid] = report.outcome
    elif report.when == "setup" and report.failed:
        _OUTCOMES[report.nodeid] = "error"


def pytest_sessionfinish(session, exitstatus):
    total = len(_OUTCOMES)
    passed = sum(1 for v in _OUTCOMES.values() if v == "passed")
    out = Path("/workspace/.foundry_tmp/hidden_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"passed": passed, "total": total, "exitstatus": exitstatus}),
        encoding="utf-8",
    )
"""


class BenchmarkResult(BaseModel):
    """Structured outcome metrics for a single foundry-bench B1 run."""

    spec_id: str
    config: Literal["B1"] = "B1"
    status: str
    g2_passed: bool = False
    hidden_api_ran: bool = False
    hidden_api_passed: int = 0
    hidden_api_total: int = 0
    hidden_api_pass_rate: float = 0.0
    cost_usd: float = 0.0
    est_value_usd: float = 0.0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0
    wall_clock_s: float = 0.0
    committed_sha: str | None = None
    model_ids: list[str] = Field(default_factory=list)
    benchmark_pin_models: bool = True
    error: str | None = None


def _pin_models(models_cfg: ModelsConfig) -> ModelsConfig:
    return models_cfg.model_copy(
        update={
            "policies": models_cfg.policies.model_copy(update={"benchmark_pin_models": True}),
        }
    )


def _benchmark_budget(budget_usd: float) -> BudgetConfig:
    cfg = BudgetConfig()
    return cfg.model_copy(
        update={
            "per_run_usd": budget_usd,
            "per_task": cfg.per_task.model_copy(update={"implement_usd": budget_usd}),
        }
    )


def _stage_hidden_tests(
    worktree_dir: Path,
    verify_workspace: Path,
    hidden_test_file: Path,
) -> None:
    sync_worktree_to_workspace(worktree_dir, verify_workspace)
    hidden_dir = verify_workspace / ".foundry_tmp" / "hidden_tests"
    hidden_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hidden_test_file, hidden_dir / "test_api_acceptance.py")
    (hidden_dir / "conftest.py").write_text(_HIDDEN_CONFTEST_PY, encoding="utf-8")


def _read_hidden_results(verify_workspace: Path) -> tuple[int, int]:
    validate_workspace_tree(verify_workspace, ignore_git_dir=False)
    results_file = verify_workspace / ".foundry_tmp" / "hidden_results.json"
    if not results_file.is_file():
        return 0, 0
    raw = json.loads(results_file.read_text(encoding="utf-8"))
    return int(raw.get("passed", 0)), int(raw.get("total", 0))


class B1BenchmarkRunner:
    """Runs Baseline B1 (Single-Agent Builder) with strict pinning and hidden API tests."""

    def __init__(
        self,
        *,
        models_cfg: ModelsConfig | None = None,
        runtime_cfg: RuntimeConfig | None = None,
        stack_pack: StackPack | None = None,
        vault: CredentialVault | None = None,
        cassettes: CassetteStore | None = None,
        sandbox_provider: SandboxProvider | None = None,
    ) -> None:
        self.models_cfg = _pin_models(models_cfg or default_models_config())
        self.runtime_cfg = runtime_cfg or default_foundry_config().runtime
        self.stack_pack: StackPack = stack_pack or FastAPIReactPostgresPack()
        self.vault = vault or CredentialVault()
        self.cassettes = cassettes
        self.sandbox_provider: SandboxProvider = sandbox_provider or LocalDockerProvider(
            self.runtime_cfg,
            HeavyExecSemaphore(self.runtime_cfg.max_heavy_exec_slots),
        )

    async def run(
        self,
        *,
        spec_dir: Path,
        out_dir: Path,
        hidden_tests_dir: Path | None = None,
        budget_usd: float = 1.50,
        max_steps: int = 25,
        max_attempts: int = 3,
        run_hidden_tests: bool = True,
        run_frontend: bool = False,
        run_schemathesis: bool = True,
    ) -> BenchmarkResult:
        resolved_spec_dir = await asyncio.to_thread(spec_dir.resolve)
        resolved_out_dir = await asyncio.to_thread(out_dir.resolve)
        await asyncio.to_thread(resolved_out_dir.mkdir, parents=True, exist_ok=True)

        spec_id = resolved_spec_dir.name
        spec_md = await asyncio.to_thread(
            (resolved_spec_dir / "spec.md").read_text, encoding="utf-8"
        )
        pinned_openapi_path = resolved_spec_dir / "pinned_openapi.yaml"
        pinned_testids_path = resolved_spec_dir / "pinned_testids.yaml"

        runner = SingleAgentRunner(
            models_cfg=self.models_cfg,
            runtime_cfg=self.runtime_cfg,
            budget_cfg=_benchmark_budget(budget_usd),
            stack_pack=self.stack_pack,
            vault=self.vault,
            cassettes=self.cassettes,
            sandbox_provider=self.sandbox_provider,
        )
        build_res: BuildResult = await runner.run(
            spec=spec_md,
            target_dir=resolved_out_dir / "build",
            app_name=spec_id,
            task_slug=spec_id,
            pinned_openapi_path=pinned_openapi_path if pinned_openapi_path.exists() else None,
            pinned_testids_path=pinned_testids_path if pinned_testids_path.exists() else None,
            max_steps=max_steps,
            max_attempts=max_attempts,
            run_frontend=run_frontend,
            run_schemathesis=run_schemathesis,
        )

        hidden_ran = False
        hidden_passed = 0
        hidden_total = 0
        final_status = build_res.status
        error = build_res.error

        resolved_hidden_dir = (
            hidden_tests_dir
            if hidden_tests_dir is not None
            else (resolved_spec_dir.parent.parent / "hidden_tests" / spec_id)
        )
        hidden_test_file = resolved_hidden_dir / "test_api_acceptance.py"
        should_run_hidden = (
            run_hidden_tests
            and build_res.g2_passed
            and hidden_test_file.is_file()
            and not isinstance(self.sandbox_provider, ReplayMockSandboxProvider)
        )

        if should_run_hidden:
            verify_ws = resolved_out_dir / "verify_workspace"
            await asyncio.to_thread(
                _stage_hidden_tests,
                build_res.worktree_dir,
                verify_ws,
                hidden_test_file,
            )
            sbx = await self.sandbox_provider.create(
                task_id=f"bench_{spec_id}_{uuid.uuid4().hex[:6]}",
                image=self.stack_pack.sandbox_image,
                workspace=verify_ws,
                env={},
            )
            try:
                exec_res = await self.sandbox_provider.exec(
                    sbx,
                    [
                        "pytest",
                        "-q",
                        "-o",
                        "cache_dir=/tmp/.pytest_cache",
                        "-o",
                        "asyncio_mode=auto",
                        ".foundry_tmp/hidden_tests/test_api_acceptance.py",
                    ],
                    network=False,
                    heavy=True,
                    priority=ExecPriority.VERIFY_G3,
                )
            finally:
                await self.sandbox_provider.destroy(sbx)

            hidden_ran = True
            hidden_passed, hidden_total = await asyncio.to_thread(_read_hidden_results, verify_ws)
            if exec_res.exit_code != 0 or hidden_total == 0 or hidden_passed < hidden_total:
                final_status = "hidden_tests_failed"
                combined = "\n".join(
                    s for s in (exec_res.stdout.strip(), exec_res.stderr.strip()) if s
                )
                error = f"Hidden API suite {hidden_passed}/{hidden_total}:\n{combined[-1200:]}"

        pass_rate = round(hidden_passed / hidden_total, 4) if hidden_total > 0 else 0.0
        result = BenchmarkResult(
            spec_id=spec_id,
            status=final_status,
            g2_passed=build_res.g2_passed,
            hidden_api_ran=hidden_ran,
            hidden_api_passed=hidden_passed,
            hidden_api_total=hidden_total,
            hidden_api_pass_rate=pass_rate,
            cost_usd=build_res.cost_usd,
            est_value_usd=build_res.est_value_usd,
            llm_calls=build_res.llm_calls,
            tokens_in=build_res.tokens_in,
            tokens_cached=build_res.tokens_cached,
            tokens_out=build_res.tokens_out,
            wall_clock_s=build_res.wall_clock_s,
            committed_sha=build_res.committed_sha,
            model_ids=build_res.model_ids,
            benchmark_pin_models=True,
            error=error,
        )
        await asyncio.to_thread(
            (resolved_out_dir / "result.json").write_text,
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return result
