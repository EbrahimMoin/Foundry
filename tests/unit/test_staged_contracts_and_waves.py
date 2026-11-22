"""Offline $0 tests for Staged Contracts (Data -> Backend -> Frontend || Infra) & Waves 1->2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from foundry.agents.graphs.lead_graph import run_staged_contract_planning
from foundry.blackboard import InMemoryBlackboardRepository, TaskStatus
from foundry.config import BudgetConfig
from foundry.contracts import (
    ComponentContract,
    ComponentSpec,
    ContractService,
    DbSchemaContract,
    RouteSpec,
    TableSpec,
    validate_staged_contracts,
)
from foundry.gateway import CassetteStore, MissPolicy, ModelGateway
from foundry.stack_packs import FastAPIReactPostgresPack
from foundry.supervisor import (
    advance_ready_tasks,
    materialize_wave_tasks,
    plan_wave1_foundation,
    plan_wave2_features,
)


def _sample_valid_openapi() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "0.1.0"},
        "components": {"schemas": {"Error": {"type": "object", "properties": {}}}},
        "paths": {
            "/api/health": {
                "get": {
                    "security": [],
                    "x-foundry-rules": "Returns 200 ok.",
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/api/notes": {
                "get": {
                    "security": [{"bearerAuth": []}],
                    "x-foundry-rules": "Lists notes owned by current user.",
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }


def test_staged_contract_validator_catches_conventions_testids_and_pinned() -> None:
    db_ok = DbSchemaContract(version=1, tables={"notes": TableSpec()})
    ui_ok = ComponentContract(
        version=1,
        routes=[RouteSpec(path="/notes", component="NotesPage", test_id="nav-notes")],
        components={
            "NotesPage": ComponentSpec(
                export_path="frontend/src/features/notes/NotesPage.tsx",
                testids=["notes-form", "notes-list"],
            )
        },
    )

    ok_res = validate_staged_contracts(
        db_schema=db_ok,
        openapi_doc=_sample_valid_openapi(),
        components=ui_ok,
        pinned_openapi=_sample_valid_openapi(),
        pinned_testids=["notes-form", "notes-list"],
    )
    assert ok_res.valid is True

    # Missing /api prefix, missing x-foundry-rules, empty DB, duplicate & missing testids
    bad_api = _sample_valid_openapi()
    bad_api["paths"]["/notes"] = {"get": {"responses": {"200": {"description": "bad"}}}}
    bad_ui = ComponentContract(
        version=1,
        routes=[],
        components={
            "A": ComponentSpec(export_path="a.tsx", testids=["dup-id"]),
            "B": ComponentSpec(export_path="b.tsx", testids=["dup-id"]),
        },
    )
    bad_res = validate_staged_contracts(
        db_schema=DbSchemaContract(version=1, tables={}),
        openapi_doc=bad_api,
        components=bad_ui,
        pinned_openapi=_sample_valid_openapi(),
        pinned_testids=["notes-form"],
    )
    assert bad_res.valid is False
    kinds = {v.kind for v in bad_res.violations}
    assert "schema_error" in kinds
    assert "testid_missing" in kinds


@pytest.mark.asyncio
async def test_staged_lead_execution_order_and_locking_from_cassettes() -> None:
    import asyncio

    spec_dir = Path("bench/specs/s1_bookmarks")
    pinned_oa_yaml = await asyncio.to_thread(
        (spec_dir / "pinned_openapi.yaml").read_text, encoding="utf-8"
    )
    pinned_oa_doc = yaml.safe_load(pinned_oa_yaml)
    pinned_ti_yaml = await asyncio.to_thread(
        (spec_dir / "pinned_testids.yaml").read_text, encoding="utf-8"
    )
    pinned_ti = list(yaml.safe_load(pinned_ti_yaml)["required_testids"])
    req_raw = await asyncio.to_thread(
        Path(
            "tests/cassettes/by_seq/orchestrator__s1_bookmarks__draft_requirements__0.json"
        ).read_text,
        encoding="utf-8",
    )
    req_json = json.loads(req_raw)["content"]
    arch_raw = await asyncio.to_thread(
        Path(
            "tests/cassettes/by_seq/orchestrator__s1_bookmarks__draft_architecture__0.json"
        ).read_text,
        encoding="utf-8",
    )
    arch_json = json.loads(arch_raw)["content"]
    spec_text = await asyncio.to_thread((spec_dir / "spec.md").read_text, encoding="utf-8")

    from foundry.config import default_models_config

    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="s1_bookmarks", budget_usd=5.0)
    contracts = ContractService(repo)
    cassettes = CassetteStore(Path("tests/cassettes"), miss_policy=MissPolicy.STRICT)
    gateway = ModelGateway(default_models_config(), cassettes=cassettes)

    outcome = await run_staged_contract_planning(
        run_id=run.id,
        task_slug="s1_bookmarks",
        spec=spec_text,
        requirements=json.loads(req_json),
        architecture=json.loads(arch_json),
        gateway=gateway,
        blackboard=repo,
        contracts_service=contracts,
        pinned_openapi=pinned_oa_doc,
        pinned_openapi_yaml=pinned_oa_yaml,
        pinned_testids=pinned_ti,
        pinned_testids_yaml=pinned_ti_yaml,
    )
    await gateway.aclose()

    # Data -> Backend -> (Frontend || Infra)
    assert outcome.execution_order[:2] == ["data", "backend"]
    assert set(outcome.execution_order[2:]) == {"frontend", "infra"}
    assert outcome.validation.valid is True
    assert all(ctr.status == "locked" for ctr, _ in outcome.contracts.values())

    # Proposing removal of pinned path must classify as pinned_violation and reject
    api_ctr, _ = outcome.contracts["api"]
    tampered_api = dict(outcome.contracts["api"][1].body)
    tampered_paths = dict(tampered_api["paths"])
    tampered_paths.pop("/api/bookmarks")
    tampered_api["paths"] = tampered_paths
    classification, violations = await contracts.propose_change(
        contract_id=api_ctr.id,
        requested_by_task_id="t_fake",
        proposed_body=tampered_api,
        reason="Drop bookmarks endpoint",
    )
    assert classification == "pinned_violation"
    assert len(violations) >= 1


@pytest.mark.asyncio
async def test_wave1_barrier_blocks_wave2_and_chains_schema_barriers() -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="demo", budget_usd=5.0)
    pack = FastAPIReactPostgresPack()
    budget_cfg = BudgetConfig()

    w1_plan = plan_wave1_foundation(app_name="demo", stack_pack=pack)
    w1_tasks, key_map = await materialize_wave_tasks(
        repo,
        run_id=run.id,
        planned_tasks=w1_plan,
        budget_cfg=budget_cfg,
    )
    assert [t.status for t in w1_tasks] == [
        TaskStatus.READY,  # F1
        TaskStatus.BLOCKED,  # F2
        TaskStatus.BLOCKED,  # F3
        TaskStatus.BLOCKED,  # F4
    ]

    w2_plan = plan_wave2_features(
        requirements={"features": [{"name": "bookmarks"}]},
        architecture={
            "features": [{"name": "bookmarks"}],
            "schema_changes": ["Add index on tags", "Add saved_views table"],
        },
        api_contract=None,
        wave1_keys=list(key_map.keys()),
        stack_pack=pack,
    )
    w2_tasks, full_map = await materialize_wave_tasks(
        repo,
        run_id=run.id,
        planned_tasks=w2_plan,
        budget_cfg=budget_cfg,
        key_to_id=key_map,
    )

    # Second schema task must explicitly depend on first schema task (linear chain A7-9)
    s1_id = full_map["W2_SCHEMA_1"]
    s2_id = full_map["W2_SCHEMA_2"]
    assert s1_id in await repo.get_task_dependencies(s2_id)
    assert all(t.status == TaskStatus.BLOCKED for t in w2_tasks)

    # Merging F1..F3 still leaves F4 non-MERGED -> Wave 2 stays strictly BLOCKED
    for k in ("F1", "F2", "F3"):
        cur = await repo.get_task(full_map[k])
        await repo.set_task_status(cur.id, TaskStatus.MERGED, cur.version)
        await advance_ready_tasks(repo, run.id)

    w2_s1 = await repo.get_task(s1_id)
    assert w2_s1.status == TaskStatus.BLOCKED

    # Once F4 merges, Wave 1 is 100% MERGED and W2_SCHEMA_1 promotes to READY (not W2_SCHEMA_2)
    f4 = await repo.get_task(full_map["F4"])
    await repo.set_task_status(f4.id, TaskStatus.MERGED, f4.version)
    await advance_ready_tasks(repo, run.id)
    assert (await repo.get_task(s1_id)).status == TaskStatus.READY
    assert (await repo.get_task(s2_id)).status == TaskStatus.BLOCKED
