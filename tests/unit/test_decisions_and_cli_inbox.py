"""Offline tests for DecisionService presets, idempotency, ADRs, and CLI Decision Inbox."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from foundry.blackboard import InMemoryBlackboardRepository
from foundry.cli.main import app
from foundry.config.schemas import AutonomyConfig, AutonomyPreset, DecisionMode
from foundry.decisions import DecisionService, is_auto_resolved, resolve_decision_mode

cli_runner = CliRunner()


def test_autonomy_presets_risk_escalation_and_unplanned_packages() -> None:
    # Balanced: strategic/tactical/release=APPROVE, operational=AUTO
    balanced = AutonomyConfig(preset=AutonomyPreset.BALANCED)
    assert resolve_decision_mode(balanced, category="strategic") == DecisionMode.APPROVE
    assert resolve_decision_mode(balanced, category="tactical") == DecisionMode.APPROVE
    assert resolve_decision_mode(balanced, category="operational") == DecisionMode.AUTO
    assert resolve_decision_mode(balanced, category="release_infra") == DecisionMode.APPROVE

    # Autopilot: operational/strategic/tactical AUTO unless low-confidence/irreversible/cloud/pkg
    auto_cfg = AutonomyConfig(preset=AutonomyPreset.AUTOPILOT)
    assert resolve_decision_mode(auto_cfg, category="strategic") == DecisionMode.AUTO
    assert (
        resolve_decision_mode(auto_cfg, category="operational", confidence=0.45)
        == DecisionMode.APPROVE
    )
    assert (
        resolve_decision_mode(auto_cfg, category="operational", reversibility="irreversible")
        == DecisionMode.APPROVE
    )
    assert (
        resolve_decision_mode(auto_cfg, category="tactical", is_unplanned_package=True)
        == DecisionMode.APPROVE
    )

    # Benchmark only allowed with local_compose
    bench_cfg = AutonomyConfig(preset=AutonomyPreset.BENCHMARK)
    assert (
        resolve_decision_mode(bench_cfg, category="release_infra", deploy_target="local_compose")
        == DecisionMode.AUTO
    )
    with pytest.raises(ValueError, match="local_compose"):
        resolve_decision_mode(bench_cfg, category="release_infra", deploy_target="aws")

    # Even Benchmark enforces hard Approve for unplanned non-curated packages (A7-10)
    assert (
        resolve_decision_mode(
            bench_cfg,
            category="tactical",
            deploy_target="local_compose",
            is_unplanned_package=True,
        )
        == DecisionMode.APPROVE
    )


@pytest.mark.asyncio
async def test_decision_service_idempotency_and_adr_creation(tmp_path: Path) -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="app", budget_usd=5.0)
    adr_dir = tmp_path / "adrs"
    svc = DecisionService(
        repo,
        autonomy_cfg=AutonomyConfig(preset=AutonomyPreset.BALANCED),
        adr_dir=adr_dir,
    )

    # Idempotent get_or_create returns exact same card on replay
    d1 = await svc.get_or_create(
        run_id=run.id,
        idempotency_key=f"{run.id}:project:approve_requirements:0",
        category="strategic",
        title="Approve reqs",
        context="Core spec summary",
        options=["approve", "reject"],
        recommendation="approve",
    )
    d2 = await svc.get_or_create(
        run_id=run.id,
        idempotency_key=f"{run.id}:project:approve_requirements:0",
        category="strategic",
        title="Approve reqs",
        context="Core spec summary",
        options=["approve", "reject"],
        recommendation="approve",
    )
    assert d1.id == d2.id
    assert not is_auto_resolved(d1)
    assert d1.status == "pending"

    resolved = await svc.resolve(d1.id, action="approve", decided_by="alice")
    assert resolved.status == "resolved"
    assert resolved.answer == {"action": "approve", "choice": "approve", "feedback": None}
    adr_file = adr_dir / f"ADR-{d1.id}.md"
    assert adr_file.is_file()
    assert "Approve reqs" in adr_file.read_text(encoding="utf-8")


def test_cli_decision_inbox_round_trip(tmp_path: Path) -> None:
    import asyncio

    state_file = tmp_path / "blackboard.json"
    adr_dir = tmp_path / "adrs"

    from foundry.blackboard import JsonFileBlackboardRepository

    async def _seed() -> str:
        repo = JsonFileBlackboardRepository(state_file)
        run = await repo.create_run(project_id="bookmarks", budget_usd=5.0)
        svc = DecisionService(repo, autonomy_cfg=AutonomyConfig(preset=AutonomyPreset.BALANCED))
        dec = await svc.get_or_create(
            run_id=run.id,
            idempotency_key=f"{run.id}:project:approve_arch:0",
            category="strategic",
            title="Approve Architecture",
            context="FastAPI + React + Postgres",
            options=["approve", "reject"],
            recommendation="approve",
        )
        return dec.id

    dec_id = asyncio.run(_seed())

    list_res = cli_runner.invoke(
        app,
        ["decisions", "list", "--state-file", str(state_file)],
    )
    assert list_res.exit_code == 0, list_res.stdout
    assert dec_id in list_res.stdout

    show_res = cli_runner.invoke(
        app,
        ["decisions", "show", dec_id, "--state-file", str(state_file)],
    )
    assert show_res.exit_code == 0, show_res.stdout
    assert "Approve Architecture" in show_res.stdout

    res_out = cli_runner.invoke(
        app,
        [
            "decisions",
            "resolve",
            dec_id,
            "--reject",
            "--feedback",
            "Add rate limiting middleware",
            "--adr-dir",
            str(adr_dir),
            "--state-file",
            str(state_file),
        ],
    )
    assert res_out.exit_code == 0, res_out.stdout
    assert "action=reject" in res_out.stdout
    assert (adr_dir / f"ADR-{dec_id}.md").is_file()

    async def _load() -> tuple[str, dict[str, str]]:
        repo_after = JsonFileBlackboardRepository(state_file)
        updated = await repo_after.get_decision(dec_id)
        return updated.status, dict(updated.answer)

    status, answer = asyncio.run(_load())
    assert status == "resolved"
    assert answer["action"] == "reject"
    assert "rate limiting" in answer["feedback"]
