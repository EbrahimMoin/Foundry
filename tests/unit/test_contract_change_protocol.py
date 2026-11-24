"""Unit tests for Section 9.4 ContractChangeProtocol (classification, batching, caps, deltas)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from foundry.blackboard import InMemoryBlackboardRepository, TaskStatus
from foundry.blackboard.models import ContractChangeRequestRecord, ContractRecord, TaskRecord
from foundry.config import AutonomyConfig, AutonomyPreset
from foundry.contracts import (
    ContractChangeProtocol,
    ContractFrozenError,
    TooManyPendingRequestsError,
)
from foundry.decisions import DecisionService


def _openapi_v1() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "App", "version": "1.0.0"},
        "paths": {
            "/api/auth/login": {"post": {"responses": {"200": {"description": "OK"}}}},
            "/api/bookmarks": {"get": {"responses": {"200": {"description": "List"}}}},
        },
    }


@pytest.mark.asyncio
async def test_contract_protocol_classification_batching_reopens_deltas_and_freeze() -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="ctr_proto", budget_usd=5.0)
    decisions = DecisionService(repo, autonomy_cfg=AutonomyConfig(preset=AutonomyPreset.AUTOPILOT))
    proto = ContractChangeProtocol(
        repo, decisions, batch_window_s=180.0, max_reopens_per_task=2, max_breaking_per_wave=3
    )

    pinned_doc = {
        "openapi": "3.1.0",
        "paths": {"/api/auth/login": {"post": {"responses": {"200": {"description": "OK"}}}}},
    }
    ctr, _ = await repo.save_contract_version(
        run_id=run.id,
        name="api",
        domain="backend",
        kind="openapi",
        body=_openapi_v1(),
        pinned_pointers=["/paths/~1api~1auth~1login"],
    )
    ctr = await repo.lock_contract(ctr.id, ctr.version, status="locked")

    t_ready = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="frontend",
        feature="bookmarks",
        title="Bookmarks UI",
        description="UI",
        write_set=["frontend/src/features/bookmarks/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.READY,
    )
    t_prog = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="bookmarks",
        title="Bookmarks API",
        description="API",
        write_set=["backend/app/features/bookmarks/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.IN_PROGRESS,
    )
    t_rev = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="frontend",
        feature="bookmarks_detail",
        title="Detail view",
        description="Detail",
        write_set=["frontend/src/features/bookmarks_detail/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.IN_REVIEW,
    )
    t_merged = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="bookmarks_export",
        title="Export bookmarks",
        description="Export",
        write_set=["backend/app/features/bookmarks_export/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.MERGED,
    )
    t_unrelated = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="frontend",
        feature="tags",
        title="Tags UI",
        description="Tags",
        write_set=["frontend/src/features/tags/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.IN_REVIEW,
    )

    for tid in (t_ready.id, t_prog.id, t_rev.id, t_merged.id):
        await repo.subscribe_task_to_contract(ctr.id, tid, scope=["/api/bookmarks"])
    await repo.subscribe_task_to_contract(ctr.id, t_unrelated.id, scope=["/api/tags"])

    # 1. Pinned pointer tampering -> rejected deterministically
    pinned_tamper = {
        **_openapi_v1(),
        "paths": {"/api/bookmarks": _openapi_v1()["paths"]["/api/bookmarks"]},
    }
    pin_res = await proto.submit_request(
        contract_id=ctr.id,
        requested_by_task_id=t_prog.id,
        proposed_body=pinned_tamper,
        reason="Remove login path",
        pinned_openapi=pinned_doc,
    )
    assert pin_res.classification == "pinned_violation"
    assert pin_res.status == "rejected"

    # 2. Additive path addition -> immediate version, zero reopens
    additive_doc = _openapi_v1()
    additive_doc["paths"]["/api/tags"] = {"get": {"responses": {"200": {"description": "Tags"}}}}
    add_res = await proto.submit_request(
        contract_id=ctr.id,
        requested_by_task_id=t_prog.id,
        proposed_body=additive_doc,
        reason="Add GET /api/tags",
        pinned_openapi=pinned_doc,
    )
    assert add_res.classification == "additive"
    assert add_res.status == "approved"
    assert (await repo.get_task(t_rev.id)).contract_reopens == 0

    # 3. Breaking change -> stays pending until batch window/flush; 2nd pending raises error
    breaking_1 = {
        **additive_doc,
        "paths": {k: v for k, v in additive_doc["paths"].items() if k != "/api/bookmarks"},
    }
    breaking_1["paths"]["/api/bookmarks"] = {
        "delete": {"responses": {"204": {"description": "Deleted"}}}
    }
    brk_res = await proto.submit_request(
        contract_id=ctr.id,
        requested_by_task_id=t_prog.id,
        proposed_body=breaking_1,
        reason="Replace GET /api/bookmarks",
        pinned_openapi=pinned_doc,
    )
    assert brk_res.classification == "breaking"
    assert brk_res.status == "pending"
    with pytest.raises(TooManyPendingRequestsError):
        await proto.submit_request(
            contract_id=ctr.id,
            requested_by_task_id=t_prog.id,
            proposed_body=breaking_1,
            reason="Duplicate pending request",
            pinned_openapi=pinned_doc,
        )

    # Not yet flushed before 3-min window unless forced
    assert await proto.flush_breaking_batch(ctr.id, force=False) is None

    # Force flush breaking batch #1
    batch1 = await proto.flush_breaking_batch(ctr.id, force=True)
    assert batch1 is not None and batch1.approved is True and batch1.contract_frozen is False
    assert t_prog.id in batch1.buffered_task_ids
    assert len(proto.pop_buffered_diffs(t_prog.id)) == 1
    assert t_rev.id in batch1.reopened_task_ids
    assert (await repo.get_task(t_rev.id)).status == TaskStatus.READY
    assert (await repo.get_task(t_rev.id)).contract_reopens == 1
    assert (await repo.get_task(t_ready.id)).contract_reopens == 0  # READY untouched
    assert (await repo.get_task(t_unrelated.id)).status == TaskStatus.IN_REVIEW  # Unscoped ignored
    assert (await repo.get_task(t_merged.id)).status == TaskStatus.MERGED
    assert len(batch1.delta_task_ids) == 1
    delta_task = await repo.get_task(batch1.delta_task_ids[0])
    assert delta_task.kind == "delta" and delta_task.parent_task_id == t_merged.id

    # Move t_rev back to IN_REVIEW and trigger breaking change #2 (reopens -> 2)
    cur_rev = await repo.get_task(t_rev.id)
    await repo.set_task_status(t_rev.id, TaskStatus.IN_REVIEW, cur_rev.version)
    breaking_2 = dict(breaking_1)
    breaking_2["paths"] = {
        **breaking_1["paths"],
        "/api/bookmarks": {"put": {"responses": {"200": {"description": "Replaced"}}}},
    }
    await proto.submit_request(
        contract_id=ctr.id,
        requested_by_task_id=t_prog.id,
        proposed_body=breaking_2,
        reason="Second breaking change",
        pinned_openapi=pinned_doc,
    )
    batch2 = await proto.flush_breaking_batch(ctr.id, force=True)
    assert batch2 is not None and batch2.approved is True
    assert (await repo.get_task(t_rev.id)).contract_reopens == 2

    # 3rd reopen on t_rev triggers FROZEN -> joint co-design -> unfrozen READY & contract frozen
    codesign_called = 0

    class _CoDesign:
        async def co_design_freeze(
            self,
            *,
            contract: ContractRecord,
            current_body: dict[str, Any],
            requests: Sequence[ContractChangeRequestRecord],
            frozen_tasks: Sequence[TaskRecord],
        ) -> dict[str, Any]:
            nonlocal codesign_called
            codesign_called += 1
            _ = (contract, requests, frozen_tasks)
            return current_body

    proto._co_design_handler = _CoDesign()
    cur_rev2 = await repo.get_task(t_rev.id)
    await repo.set_task_status(t_rev.id, TaskStatus.IN_REVIEW, cur_rev2.version)
    breaking_3 = dict(breaking_2)
    breaking_3["paths"] = {
        **breaking_2["paths"],
        "/api/bookmarks": {"patch": {"responses": {"200": {"description": "Patched"}}}},
    }
    await proto.submit_request(
        contract_id=ctr.id,
        requested_by_task_id=t_prog.id,
        proposed_body=breaking_3,
        reason="Third breaking change triggers freeze",
        pinned_openapi=pinned_doc,
    )
    batch3 = await proto.flush_breaking_batch(ctr.id, force=True)
    assert batch3 is not None and batch3.contract_frozen is True
    assert codesign_called == 1
    assert t_rev.id in batch3.frozen_task_ids
    assert (await repo.get_task(t_rev.id)).status == TaskStatus.READY
    assert (await repo.get_contract_by_id(ctr.id)).status == "frozen"

    with pytest.raises(ContractFrozenError):
        await proto.submit_request(
            contract_id=ctr.id,
            requested_by_task_id=t_prog.id,
            proposed_body=breaking_3,
            reason="Blocked after freeze",
        )


@pytest.mark.asyncio
async def test_max_breaking_versions_per_wave_freezes_contract_and_escalates() -> None:
    repo = InMemoryBlackboardRepository()
    run = await repo.create_run(project_id="wave_cap", budget_usd=5.0)
    decisions = DecisionService(repo, autonomy_cfg=AutonomyConfig(preset=AutonomyPreset.AUTOPILOT))
    proto = ContractChangeProtocol(repo, decisions, max_reopens_per_task=5, max_breaking_per_wave=3)

    ctr, _ = await repo.save_contract_version(
        run_id=run.id,
        name="api",
        domain="backend",
        kind="openapi",
        body=_openapi_v1(),
    )
    ctr = await repo.lock_contract(ctr.id, ctr.version, status="locked")
    requester = await repo.create_task(
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="bookmarks",
        title="Requester task",
        description="Requester",
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.IN_PROGRESS,
    )

    # Trigger 3 approved breaking versions in the wave (breaking_in_wave becomes 3)
    for idx, method in enumerate(("delete", "put", "patch"), start=1):
        proposed = _openapi_v1()
        proposed["paths"]["/api/bookmarks"] = {
            method: {"responses": {"200": {"description": f"Break {idx}"}}}
        }
        await proto.submit_request(
            contract_id=ctr.id,
            requested_by_task_id=requester.id,
            proposed_body=proposed,
            reason=f"Breaking change #{idx}",
        )
        res = await proto.flush_breaking_batch(ctr.id, force=True)
        assert res is not None and res.approved is True

    assert (await repo.get_contract_by_id(ctr.id)).breaking_in_wave == 3

    # A 4th breaking request hits max_breaking_per_wave -> freezes contract & escalates
    fourth = _openapi_v1()
    fourth["paths"]["/api/bookmarks"] = {
        "options": {"responses": {"200": {"description": "Break 4"}}}
    }
    await proto.submit_request(
        contract_id=ctr.id,
        requested_by_task_id=requester.id,
        proposed_body=fourth,
        reason="Breaking change #4 exceeds wave limit",
    )
    res4 = await proto.flush_breaking_batch(ctr.id, force=True)
    assert res4 is not None
    assert res4.approved is False
    assert res4.contract_frozen is True
    assert res4.reason == "max_breaking_per_wave_exceeded"
    assert res4.decision_id is not None
    assert (await repo.get_contract_by_id(ctr.id)).status == "frozen"
