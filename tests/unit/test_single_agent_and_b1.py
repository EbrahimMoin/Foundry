"""Offline $0 unit tests for SingleAgentBuilder (v0.1) and Baseline B1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bench.runner import B1BenchmarkRunner

from foundry.agents.single import ReplayMockSandboxProvider, SingleAgentRunner
from foundry.config import BudgetConfig, ProviderConfig, default_models_config
from foundry.gateway import (
    CassetteStore,
    GatewayRequest,
    GatewayResponse,
    MissPolicy,
    ModelGateway,
    RunBudgetState,
    ToolCallSpec,
    TransportHealth,
)
from foundry.security import CredentialVault


class _ScriptedTransport:
    name = "scripted"

    def __init__(self, steps: list[GatewayResponse]) -> None:
        self._steps = list(steps)
        self.seen_requests: list[tuple[GatewayRequest, str]] = []

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        _ = (provider_id, provider_cfg)
        self.seen_requests.append((request, model))
        idx = min(len(self.seen_requests) - 1, len(self._steps) - 1)
        return self._steps[idx]

    async def health_check(self, provider_id: str, provider_cfg: ProviderConfig) -> TransportHealth:
        return TransportHealth(
            provider_id=provider_id,
            transport=provider_cfg.transport,
            healthy=True,
            detail="scripted",
        )


@pytest.mark.asyncio
async def test_single_agent_s1_bookmarks_replay_happy_path(tmp_path: Path) -> None:
    spec_dir = Path("bench/specs/s1_bookmarks")
    spec_text = (spec_dir / "spec.md").read_text(encoding="utf-8")
    cassettes = CassetteStore(Path("tests/cassettes"), miss_policy=MissPolicy.STRICT)
    provider = ReplayMockSandboxProvider()

    runner = SingleAgentRunner(
        cassettes=cassettes,
        sandbox_provider=provider,
    )
    res = await runner.run(
        spec=spec_text,
        target_dir=tmp_path / "build_s1",
        app_name="s1_bookmarks",
        task_slug="s1_bookmarks",
        pinned_openapi_path=spec_dir / "pinned_openapi.yaml",
        pinned_testids_path=spec_dir / "pinned_testids.yaml",
    )

    assert res.status == "passed"
    assert res.g2_passed is True
    assert res.committed_sha is not None and len(res.committed_sha) == 40
    assert res.cost_usd == 0.0
    assert res.llm_calls == 3
    assert (res.worktree_dir / "backend" / "app" / "features" / "bookmarks" / "router.py").exists()
    assert (
        res.worktree_dir / "frontend" / "src" / "features" / "bookmarks" / "BookmarkPage.tsx"
    ).exists()
    assert not (res.workspace_dir / ".git").exists()


@pytest.mark.asyncio
async def test_single_agent_g2_failure_retries_and_upgrades_model(tmp_path: Path) -> None:
    models_cfg = default_models_config()
    transport_kind = models_cfg.providers["gemini_paid"].transport
    scripted = _ScriptedTransport(
        [
            # Attempt 1: try out-of-write-set path, then valid write & finish
            GatewayResponse(
                provider_id="gemini_paid",
                model="gemini-2.5-flash-lite",
                transport=transport_kind,
                billing_mode=models_cfg.providers["gemini_paid"].billing_mode,
                tool_calls=[
                    ToolCallSpec(
                        id="c1",
                        name="write_file",
                        args={"path": "infra/hack.txt", "content": "nope"},
                    ),
                    ToolCallSpec(
                        id="c2",
                        name="write_file",
                        args={
                            "path": "backend/app/features/notes/router.py",
                            "content": "from fastapi import APIRouter\nrouter = APIRouter()\n",
                        },
                    ),
                    ToolCallSpec(id="c3", name="finish_build", args={"summary": "attempt 1"}),
                ],
                tokens_in=100,
                tokens_out=50,
            ),
            # Attempt 2 (after G2 fail): finish again once fail_once_commands cleared
            GatewayResponse(
                provider_id="gemini_paid",
                model="gemini-2.5-flash",
                transport=transport_kind,
                billing_mode=models_cfg.providers["gemini_paid"].billing_mode,
                tool_calls=[
                    ToolCallSpec(id="c4", name="finish_build", args={"summary": "attempt 2"}),
                ],
                tokens_in=100,
                tokens_out=20,
            ),
        ]
    )
    vault = CredentialVault(
        vault_path=tmp_path / "vault.enc", passphrase="test", backend_order=["encrypted_vault"]
    )
    vault.set_secret("gemini", "AIzaDummyKeyForOfflineScriptedTest000001")
    budget_cfg = BudgetConfig(per_run_usd=5.0)
    ledger = RunBudgetState(budget_cfg)
    gateway = ModelGateway(
        models_cfg,
        vault=vault,
        transports={transport_kind: scripted},
        run_budget=ledger,
    )

    provider = ReplayMockSandboxProvider(fail_once_commands={"pytest"})
    runner = SingleAgentRunner(
        models_cfg=models_cfg,
        budget_cfg=budget_cfg,
        gateway=gateway,
        sandbox_provider=provider,
    )
    res = await runner.run(
        spec="Build a notes feature",
        target_dir=tmp_path / "retry_build",
        app_name="notes-app",
        task_slug="notes",
        write_set=["backend/"],
        max_attempts=3,
    )

    assert res.status == "passed"
    assert res.attempts == 2
    assert len(scripted.seen_requests) == 2
    # Attempt 1 used @coder (gemini-2.5-flash-lite);
    # Attempt 2 upgraded to @coder_plus (gemini-2.5-flash)
    assert scripted.seen_requests[0][1] == "gemini-2.5-flash-lite"
    assert scripted.seen_requests[1][1] == "gemini-2.5-flash"

    # Verify tool error from out-of-write-set write was safely returned to the model on step 2
    tool_msgs = [m for m in scripted.seen_requests[1][0].messages if m.get("role") == "tool"]
    first_tool_payload = json.loads(tool_msgs[0]["content"])
    assert first_tool_payload["ok"] is False
    assert "write set" in first_tool_payload["error"].lower()


@pytest.mark.asyncio
async def test_single_agent_stops_cleanly_on_budget_exceeded(tmp_path: Path) -> None:
    models_cfg = default_models_config()
    transport_kind = models_cfg.providers["gemini_paid"].transport
    budget_cfg = BudgetConfig(per_run_usd=0.0)
    ledger = RunBudgetState(budget_cfg)
    gateway = ModelGateway(
        models_cfg,
        transports={transport_kind: _ScriptedTransport([])},
        run_budget=ledger,
    )
    runner = SingleAgentRunner(
        models_cfg=models_cfg,
        budget_cfg=budget_cfg,
        gateway=gateway,
        sandbox_provider=ReplayMockSandboxProvider(),
    )
    res = await runner.run(
        spec="Zero budget spec",
        target_dir=tmp_path / "budget_build",
        app_name="budget-app",
        task_slug="budget",
    )
    assert res.status == "budget_exceeded"
    assert res.g2_passed is False
    assert res.error is not None and "budget cap" in res.error.lower()


@pytest.mark.asyncio
async def test_b1_benchmark_runner_offline_replay_writes_result_json(tmp_path: Path) -> None:
    cassettes = CassetteStore(Path("tests/cassettes"), miss_policy=MissPolicy.STRICT)
    provider = ReplayMockSandboxProvider()
    b1 = B1BenchmarkRunner(cassettes=cassettes, sandbox_provider=provider)

    assert b1.models_cfg.policies.benchmark_pin_models is True

    out_dir = tmp_path / "b1_out"
    res = await b1.run(
        spec_dir=Path("bench/specs/s1_bookmarks"),
        out_dir=out_dir,
        budget_usd=1.50,
    )

    assert res.status == "passed"
    assert res.config == "B1"
    assert res.spec_id == "s1_bookmarks"
    assert res.g2_passed is True
    assert res.hidden_api_ran is False
    assert res.cost_usd == 0.0
    assert res.benchmark_pin_models is True
    assert (out_dir / "result.json").is_file()
    saved: dict[str, Any] = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
    assert saved["g2_passed"] is True
    assert saved["benchmark_pin_models"] is True
