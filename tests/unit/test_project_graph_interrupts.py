"""Offline $0 tests for re-run-safe ProjectGraph interrupts and end-to-end RunSupervisor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from foundry.agents.graphs.project_graph import (
    ProjectGraphDeps,
    ProjectState,
    build_project_graph,
)
from foundry.blackboard import InMemoryBlackboardRepository, TaskStatus
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
from foundry.supervisor import JsonFileCheckpointSaver, RunSupervisor


class _CountingOrchestratorTransport:
    name = "counting"

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
            body = {
                "summary": f"Reqs rev {request.step_index}",
                "features": [{"name": "bookmarks", "description": "CRUD bookmarks"}],
                "roles": ["member"],
            }
        else:
            body = {
                "stack_pack": "fastapi-react-postgres",
                "auth_scheme": "bearer_jwt",
                "features": [{"name": "bookmarks"}],
                "schema_changes": [],
            }
        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=provider_cfg.transport,
            billing_mode=provider_cfg.billing_mode,
            content=json.dumps(body),
            tokens_in=50,
            tokens_out=30,
        )

    async def health_check(self, provider_id: str, provider_cfg: ProviderConfig) -> TransportHealth:
        return TransportHealth(
            provider_id=provider_id,
            transport=provider_cfg.transport,
            healthy=True,
            detail="counting",
        )


class _StubHooks:
    async def ensure_contract_planning(self, run_id: str, state: dict[str, Any]) -> int | None:
        _ = (run_id, state)
        return 1

    async def ensure_wave1(self, run_id: str, state: dict[str, Any]) -> bool:
        _ = (run_id, state)
        return True

    async def ensure_wave2(self, run_id: str, state: dict[str, Any]) -> bool:
        _ = (run_id, state)
        return True


@pytest.mark.asyncio
async def test_project_graph_rerun_safe_interrupt_and_reject_feedback_loop() -> None:
    models_cfg = default_models_config()
    transport = _CountingOrchestratorTransport()
    prov_transport = models_cfg.providers["gemini_paid"].transport
    gateway = ModelGateway(models_cfg, transports={prov_transport: transport})

    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="bookmarks", budget_usd=5.0)
    decisions = DecisionService(repo, autonomy_cfg=AutonomyConfig(preset=AutonomyPreset.BALANCED))

    graph = build_project_graph(
        ProjectGraphDeps(
            gateway=gateway,
            decisions=decisions,
            blackboard=repo,
            supervisor_hooks=_StubHooks(),
        ),
        checkpointer=MemorySaver(),
    )
    cfg = {"configurable": {"thread_id": f"{run.id}:project"}}
    init_state: ProjectState = {
        "run_id": run.id,
        "task_slug": "bookmarks",
        "app_name": "bookmarks",
        "spec": "Build a bookmarks app",
        "req_revision": 0,
        "arch_revision": 0,
        "phase": "planning",
    }

    # First run pauses on approve_requirements; draft_requirements ran exactly once
    out1 = await graph.ainvoke(init_state, cfg)
    assert "__interrupt__" in out1
    assert transport.node_calls["draft_requirements"] == 1
    pending = await repo.list_decisions(run_id=run.id, status="pending")
    assert len(pending) == 1

    # Approve via DecisionService & resume -> approve_requirements re-runs WITHOUT re-calling draft!
    await decisions.resolve(pending[0].id, action="approve", decided_by="alice")
    out2 = await graph.ainvoke(Command(resume={"action": "approve"}), cfg)
    assert transport.node_calls["draft_requirements"] == 1  # Strictly NOT repeated!
    assert len(await repo.list_decisions(run_id=run.id)) == 2  # reqs + arch, no duplicate card!
    assert "__interrupt__" in out2

    # Reject architecture with feedback -> increments arch_revision -> new card :1 -> approve
    pending_arch = await repo.list_decisions(run_id=run.id, status="pending")
    assert len(pending_arch) == 1
    out3 = await graph.ainvoke(
        Command(resume={"action": "reject", "feedback": "Add Redis-free cache note"}),
        cfg,
    )
    assert transport.node_calls["draft_architecture"] == 2
    assert "__interrupt__" in out3

    out4 = await graph.ainvoke(Command(resume={"action": "approve"}), cfg)
    assert out4["phase"] == "done"
    assert out4["wave1_completed"] is True
    assert out4["wave2_completed"] is True


@pytest.mark.asyncio
async def test_run_supervisor_end_to_end_s1_with_replay_and_disk_checkpoint(tmp_path: Path) -> None:
    spec_dir = Path("bench/specs/s1_bookmarks")
    spec_text = (spec_dir / "spec.md").read_text(encoding="utf-8")
    pinned_oa_yaml = (spec_dir / "pinned_openapi.yaml").read_text(encoding="utf-8")
    pinned_oa_doc = yaml.safe_load(pinned_oa_yaml)
    pinned_ti_yaml = (spec_dir / "pinned_testids.yaml").read_text(encoding="utf-8")
    pinned_ti = list(yaml.safe_load(pinned_ti_yaml)["required_testids"])

    cassettes = CassetteStore(Path("tests/cassettes"), miss_policy=MissPolicy.STRICT)
    repo = InMemoryBlackboardRepository()
    ckpt_path = tmp_path / "run.ckpt.json"
    executed_order: list[tuple[int, str]] = []

    async def _track_executor(task: Any, ctx: dict[str, Any]) -> None:
        _ = ctx
        executed_order.append((task.wave, task.domain))

    supervisor = RunSupervisor(
        repo,
        autonomy_cfg=AutonomyConfig(preset=AutonomyPreset.BALANCED),
        cassettes=cassettes,
        checkpointer=JsonFileCheckpointSaver(ckpt_path),
        adr_dir=tmp_path / "adrs",
        task_executor=_track_executor,
    )

    s1 = await supervisor.start_run(
        spec=spec_text,
        app_name="s1_bookmarks",
        task_slug="s1_bookmarks",
        pinned_openapi=pinned_oa_doc,
        pinned_openapi_yaml=pinned_oa_yaml,
        pinned_testids=pinned_ti,
        pinned_testids_yaml=pinned_ti_yaml,
    )
    assert s1.status == "waiting_decision"
    assert len(s1.pending_decisions) == 1

    # Approve requirements -> pauses on approve_architecture
    await supervisor.decisions.resolve(s1.pending_decisions[0].id, action="approve")

    # Instantiate fresh supervisor reloading JsonFileCheckpointSaver from disk (simulates restart)
    supervisor2 = RunSupervisor(
        repo,
        autonomy_cfg=AutonomyConfig(preset=AutonomyPreset.BALANCED),
        cassettes=cassettes,
        checkpointer=JsonFileCheckpointSaver(ckpt_path),
        adr_dir=tmp_path / "adrs",
        task_executor=_track_executor,
    )
    s2 = await supervisor2.resume_run(
        s1.run_id,
        pinned_openapi=pinned_oa_doc,
        pinned_openapi_yaml=pinned_oa_yaml,
        pinned_testids=pinned_ti,
        pinned_testids_yaml=pinned_ti_yaml,
    )
    assert s2.status == "waiting_decision"
    assert len(s2.pending_decisions) == 1

    # Approve architecture -> runs Staged Leads -> Locks v1 -> Wave 1 -> Wave 2 -> done!
    await supervisor2.decisions.resolve(s2.pending_decisions[0].id, action="approve")
    s3 = await supervisor2.resume_run(
        s1.run_id,
        pinned_openapi=pinned_oa_doc,
        pinned_openapi_yaml=pinned_oa_yaml,
        pinned_testids=pinned_ti,
        pinned_testids_yaml=pinned_ti_yaml,
    )

    assert s3.status == "completed"
    assert s3.phase == "done"
    assert len(s3.contracts) == 4
    assert all(c.status == "locked" for c in s3.contracts)

    wave1_tasks = [t for t in s3.tasks if t.wave == 1]
    wave2_tasks = [t for t in s3.tasks if t.wave == 2]
    assert len(wave1_tasks) == 4
    assert len(wave2_tasks) >= 4
    assert all(t.status == TaskStatus.MERGED for t in s3.tasks)

    # Strict Wave 1 -> Wave 2 execution order check
    waves_seen = [w for w, _ in executed_order]
    last_w1 = max(i for i, w in enumerate(waves_seen) if w == 1)
    first_w2 = min(i for i, w in enumerate(waves_seen) if w == 2)
    assert last_w1 < first_w2
