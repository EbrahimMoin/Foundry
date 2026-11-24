"""Contract change classification, 3-min batching, freeze caps, and delta tasks (Sec 9.4)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field
from ulid import ULID

from foundry.blackboard.models import (
    ContractChangeRequestRecord,
    ContractRecord,
    TaskRecord,
    TaskStatus,
)
from foundry.blackboard.repository import BlackboardRepository
from foundry.contracts.service import ContractService
from foundry.decisions.service import DecisionService
from foundry.verification.contract_checks import check_pinned_pointers, diff_openapi_breaking
from foundry.verification.models import ContractViolation

MAX_REOPENS_PER_TASK = 2
MAX_BREAKING_PER_WAVE = 3
DEFAULT_BATCH_WINDOW_S = 180.0


class ContractFrozenError(ValueError):
    """Raised when attempting to modify a contract already frozen for the wave."""


class TooManyPendingRequestsError(ValueError):
    """Raised when a task submits a second breaking request while one is pending (Sec 9.4)."""


@runtime_checkable
class LeadBatchApprover(Protocol):
    """Owning Domain Lead approval hook for batched breaking contract changes."""

    async def review_breaking_batch(
        self,
        *,
        contract: ContractRecord,
        current_body: dict[str, Any],
        proposed_body: dict[str, Any],
        requests: Sequence[ContractChangeRequestRecord],
    ) -> tuple[bool, str]: ...


@runtime_checkable
class JointCoDesignHandler(Protocol):
    """Single joint Lead co-design hook triggered when a task hits its 3rd contract reopen."""

    async def co_design_freeze(
        self,
        *,
        contract: ContractRecord,
        current_body: dict[str, Any],
        requests: Sequence[ContractChangeRequestRecord],
        frozen_tasks: Sequence[TaskRecord],
    ) -> dict[str, Any]: ...


class ContractSubmissionResult(BaseModel):
    """Outcome of submitting a contract change request."""

    request: ContractChangeRequestRecord
    classification: Literal["pinned_violation", "additive", "breaking"]
    status: str
    violations: list[ContractViolation] = Field(default_factory=list)
    changed_elements: list[str] = Field(default_factory=list)


class ContractBatchResult(BaseModel):
    """Outcome of flushing batched breaking contract change requests."""

    contract_id: str
    batch_id: str
    approved: bool
    new_version: int | None = None
    contract_frozen: bool = False
    reopened_task_ids: list[str] = Field(default_factory=list)
    buffered_task_ids: list[str] = Field(default_factory=list)
    frozen_task_ids: list[str] = Field(default_factory=list)
    delta_task_ids: list[str] = Field(default_factory=list)
    decision_id: str | None = None
    reason: str = ""


def _extract_changed_elements(
    old_body: dict[str, Any], new_body: dict[str, Any], kind: str
) -> list[str]:
    changed: list[str] = []
    if kind == "openapi":
        old_paths = old_body.get("paths", {}) or {}
        new_paths = new_body.get("paths", {}) or {}
        for p in sorted(set(old_paths) | set(new_paths)):
            if old_paths.get(p) != new_paths.get(p):
                changed.append(str(p))
        old_schemas = (old_body.get("components") or {}).get("schemas", {}) or {}
        new_schemas = (new_body.get("components") or {}).get("schemas", {}) or {}
        for s in sorted(set(old_schemas) | set(new_schemas)):
            if old_schemas.get(s) != new_schemas.get(s):
                changed.append(f"schema:{s}")
    elif kind == "db_schema":
        old_tables = {
            str(t.get("name")): t for t in old_body.get("tables", []) or [] if "name" in t
        }
        new_tables = {
            str(t.get("name")): t for t in new_body.get("tables", []) or [] if "name" in t
        }
        for name in sorted(set(old_tables) | set(new_tables)):
            if old_tables.get(name) != new_tables.get(name):
                changed.append(f"table:{name}")
    elif kind == "component":
        old_comps = {
            str(c.get("name")): c for c in old_body.get("components", []) or [] if "name" in c
        }
        new_comps = {
            str(c.get("name")): c for c in new_body.get("components", []) or [] if "name" in c
        }
        for name in sorted(set(old_comps) | set(new_comps)):
            if old_comps.get(name) != new_comps.get(name):
                changed.append(f"component:{name}")
    else:
        for k in sorted(set(old_body) | set(new_body)):
            if old_body.get(k) != new_body.get(k):
                changed.append(str(k))
    return changed


def _is_non_openapi_breaking(
    old_body: dict[str, Any], new_body: dict[str, Any], kind: str
) -> list[ContractViolation]:
    violations: list[ContractViolation] = []
    if kind == "db_schema":
        old_tables = {
            str(t.get("name")): t for t in old_body.get("tables", []) or [] if "name" in t
        }
        new_tables = {
            str(t.get("name")): t for t in new_body.get("tables", []) or [] if "name" in t
        }
        for tname, old_tbl in old_tables.items():
            if tname not in new_tables:
                violations.append(
                    ContractViolation(
                        kind="breaking_change",
                        pointer=f"/tables/{tname}",
                        message=f"Table '{tname}' was removed.",
                    )
                )
                continue
            old_cols = {
                str(c.get("name")): c for c in old_tbl.get("columns", []) or [] if "name" in c
            }
            new_cols = {
                str(c.get("name")): c
                for c in new_tables[tname].get("columns", []) or []
                if "name" in c
            }
            for cname, old_c in old_cols.items():
                if cname not in new_cols or new_cols[cname].get("type") != old_c.get("type"):
                    violations.append(
                        ContractViolation(
                            kind="breaking_change",
                            pointer=f"/tables/{tname}/columns/{cname}",
                            message=f"Column '{tname}.{cname}' removed or changed type.",
                        )
                    )
    elif kind == "component":
        old_comps = {
            str(c.get("name")): c for c in old_body.get("components", []) or [] if "name" in c
        }
        new_comps = {
            str(c.get("name")): c for c in new_body.get("components", []) or [] if "name" in c
        }
        for cname, old_c in old_comps.items():
            if cname not in new_comps:
                violations.append(
                    ContractViolation(
                        kind="breaking_change",
                        pointer=f"/components/{cname}",
                        message=f"Component '{cname}' removed.",
                    )
                )
                continue
            new_c = new_comps[cname]
            if new_c.get("export_path") != old_c.get("export_path"):
                violations.append(
                    ContractViolation(
                        kind="breaking_change",
                        pointer=f"/components/{cname}/export_path",
                        message=f"Component '{cname}' export_path changed.",
                    )
                )
            old_ids = set(old_c.get("data_testids", []) or [])
            new_ids = set(new_c.get("data_testids", []) or [])
            if not old_ids.issubset(new_ids):
                removed = sorted(old_ids - new_ids)
                violations.append(
                    ContractViolation(
                        kind="breaking_change",
                        pointer=f"/components/{cname}/data_testids",
                        message=f"Component '{cname}' removed data-testid(s): {removed}",
                    )
                )
    return violations


def _scope_overlaps(scope: Sequence[str], changed_elements: Sequence[str]) -> bool:
    if not scope or not changed_elements:
        return True
    for s in scope:
        for elem in changed_elements:
            if s == elem or elem.startswith(s) or s.startswith(elem):
                return True
    return False


class ContractChangeProtocol:
    """Implements Section 9.4 contract change classification, batching, freeze caps & deltas."""

    def __init__(
        self,
        repo: BlackboardRepository,
        decisions: DecisionService,
        *,
        contracts_service: ContractService | None = None,
        lead_approver: LeadBatchApprover | None = None,
        co_design_handler: JointCoDesignHandler | None = None,
        batch_window_s: float = DEFAULT_BATCH_WINDOW_S,
        max_reopens_per_task: int = MAX_REOPENS_PER_TASK,
        max_breaking_per_wave: int = MAX_BREAKING_PER_WAVE,
    ) -> None:
        self._repo = repo
        self._decisions = decisions
        self._contracts = contracts_service or ContractService(repo)
        self._lead_approver = lead_approver
        self._co_design_handler = co_design_handler
        self.batch_window_s = batch_window_s
        self.max_reopens_per_task = max_reopens_per_task
        self.max_breaking_per_wave = max_breaking_per_wave
        self._batch_opened_at: dict[str, datetime] = {}
        self._buffered_diffs: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def pop_buffered_diffs(self, task_id: str) -> list[dict[str, Any]]:
        """Consume buffered contract diffs queued for an IN_PROGRESS task at loop boundary."""
        items = list(self._buffered_diffs.pop(task_id, []))
        return items

    def peek_buffered_diffs(self, task_id: str) -> list[dict[str, Any]]:
        return list(self._buffered_diffs.get(task_id, []))

    async def submit_request(
        self,
        *,
        contract_id: str,
        requested_by_task_id: str,
        proposed_body: dict[str, Any],
        reason: str,
        pinned_openapi: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ContractSubmissionResult:
        contract = await self._repo.get_contract_by_id(contract_id)
        if contract.status == "frozen":
            raise ContractFrozenError(
                f"Contract '{contract.name}' ({contract_id}) is frozen for the wave."
            )

        existing = await self._repo.list_contract_change_requests(contract_id)
        if any(
            r.requested_by == requested_by_task_id and r.status in {"pending", "batched"}
            for r in existing
        ):
            raise TooManyPendingRequestsError(
                f"Task {requested_by_task_id} already has a pending request on {contract.name}."
            )

        current_ver = await self._repo.get_latest_contract_version(contract_id)
        changed_elements = _extract_changed_elements(current_ver.body, proposed_body, contract.kind)

        # 1. Deterministic pinned-pointer check
        violations: list[ContractViolation] = []
        if contract.kind == "openapi":
            if pinned_openapi is not None:
                violations.extend(check_pinned_pointers(pinned_openapi, proposed_body))
            elif current_ver.pinned_pointers:
                pinned_set = set(current_ver.pinned_pointers)
                for v in check_pinned_pointers(current_ver.body, proposed_body):
                    if any(
                        v.pointer == p
                        or v.pointer.startswith(f"{p}/")
                        or p.startswith(f"{v.pointer}/")
                        for p in pinned_set
                    ):
                        violations.append(v)

        if violations:
            req = await self._repo.create_contract_change_request(
                contract_id=contract_id,
                requested_by=requested_by_task_id,
                proposed_body=proposed_body,
                reason=reason,
                classified_as="pinned_violation",
                status="rejected",
            )
            return ContractSubmissionResult(
                request=req,
                classification="pinned_violation",
                status="rejected",
                violations=violations,
                changed_elements=changed_elements,
            )

        # 2. Breaking vs. Additive check
        if contract.kind == "openapi":
            breaking = diff_openapi_breaking(current_ver.body, proposed_body)
        else:
            breaking = _is_non_openapi_breaking(current_ver.body, proposed_body, contract.kind)

        if not breaking:
            req = await self._repo.create_contract_change_request(
                contract_id=contract_id,
                requested_by=requested_by_task_id,
                proposed_body=proposed_body,
                reason=reason,
                classified_as="additive",
                status="approved",
            )
            await self._repo.save_contract_version(
                run_id=contract.run_id,
                name=contract.name,
                domain=contract.domain,
                kind=contract.kind,
                body=proposed_body,
                pinned_pointers=current_ver.pinned_pointers,
                change_kind="additive",
                changed_elements=changed_elements,
                change_summary=reason,
            )
            return ContractSubmissionResult(
                request=req,
                classification="additive",
                status="approved",
                violations=[],
                changed_elements=changed_elements,
            )

        req = await self._repo.create_contract_change_request(
            contract_id=contract_id,
            requested_by=requested_by_task_id,
            proposed_body=proposed_body,
            reason=reason,
            classified_as="breaking",
            status="pending",
        )
        self._batch_opened_at.setdefault(contract_id, now or datetime.now(UTC))
        return ContractSubmissionResult(
            request=req,
            classification="breaking",
            status="pending",
            violations=breaking,
            changed_elements=changed_elements,
        )

    async def flush_breaking_batch(
        self,
        contract_id: str,
        *,
        force: bool = False,
        now: datetime | None = None,
    ) -> ContractBatchResult | None:
        """Batch pending breaking requests, check wave/reopen caps, and apply subscriber actions."""
        all_reqs = await self._repo.list_contract_change_requests(contract_id)
        pending = [r for r in all_reqs if r.classified_as == "breaking" and r.status == "pending"]
        if not pending:
            return None

        cur_time = now or datetime.now(UTC)
        opened = self._batch_opened_at.get(contract_id, pending[0].created_at)
        if not force and (cur_time - opened).total_seconds() < self.batch_window_s:
            return None

        contract = await self._repo.get_contract_by_id(contract_id)
        batch_id = f"batch_{str(ULID()).lower()}"
        for req in pending:
            await self._repo.update_contract_change_request(
                req.id, status="batched", batch_id=batch_id
            )
        self._batch_opened_at.pop(contract_id, None)

        await self._repo.emit_event(
            run_id=contract.run_id,
            event_type="contract.batched",
            subject_id=contract_id,
            payload={
                "contract_id": contract_id,
                "batch_id": batch_id,
                "request_ids": [r.id for r in pending],
            },
        )

        # Wave breaking cap: max 3 breaking versions per contract per wave
        if contract.breaking_in_wave >= self.max_breaking_per_wave:
            await self._repo.lock_contract(contract_id, contract.version, status="frozen")
            for req in pending:
                await self._repo.update_contract_change_request(
                    req.id, status="escalated", batch_id=batch_id
                )
            dec = await self._decisions.get_or_create(
                run_id=contract.run_id,
                idempotency_key=f"contract_freeze:{contract_id}:{batch_id}",
                category="tactical",
                title=f"Contract '{contract.name}' reached max breaking revisions in wave",
                context=(
                    f"Contract {contract.name} hit {contract.breaking_in_wave} breaking updates; "
                    "escalated to Orchestrator/Human."
                ),
                options=["keep_frozen", "allow_override"],
                recommendation="keep_frozen",
                confidence=0.9,
            )
            return ContractBatchResult(
                contract_id=contract_id,
                batch_id=batch_id,
                approved=False,
                contract_frozen=True,
                decision_id=dec.id,
                reason="max_breaking_per_wave_exceeded",
            )

        # Merge proposed bodies sequentially in FIFO order
        current_ver = await self._repo.get_latest_contract_version(contract_id)
        consolidated_body = dict(pending[-1].proposed_body)
        changed_elements = _extract_changed_elements(
            current_ver.body, consolidated_body, contract.kind
        )

        # Tactical decision / Lead ruling
        dec = await self._decisions.get_or_create(
            run_id=contract.run_id,
            idempotency_key=f"contract_batch:{contract_id}:{batch_id}",
            category="tactical",
            title=f"Approve breaking change batch on '{contract.name}'",
            context="; ".join(r.reason for r in pending),
            options=["approve", "reject"],
            recommendation="approve",
            confidence=0.9,
        )
        if dec.status == "pending":
            return ContractBatchResult(
                contract_id=contract_id,
                batch_id=batch_id,
                approved=False,
                decision_id=dec.id,
                reason="awaiting_human_decision",
            )
        if (
            dec.status == "resolved"
            and isinstance(dec.answer, dict)
            and (dec.answer.get("action") == "reject")
        ):
            for req in pending:
                await self._repo.update_contract_change_request(
                    req.id, status="rejected", batch_id=batch_id
                )
            return ContractBatchResult(
                contract_id=contract_id,
                batch_id=batch_id,
                approved=False,
                decision_id=dec.id,
                reason=str(dec.answer.get("feedback") or "rejected_by_human"),
            )

        if self._lead_approver is not None:
            ok, lead_reason = await self._lead_approver.review_breaking_batch(
                contract=contract,
                current_body=current_ver.body,
                proposed_body=consolidated_body,
                requests=pending,
            )
            if not ok:
                for req in pending:
                    await self._repo.update_contract_change_request(
                        req.id, status="rejected", batch_id=batch_id
                    )
                return ContractBatchResult(
                    contract_id=contract_id,
                    batch_id=batch_id,
                    approved=False,
                    decision_id=dec.id,
                    reason=lead_reason,
                )

        # Approve batch and publish ONE new consolidated breaking version
        for req in pending:
            await self._repo.update_contract_change_request(
                req.id, status="approved", batch_id=batch_id
            )
        updated_ctr, new_ver = await self._repo.save_contract_version(
            run_id=contract.run_id,
            name=contract.name,
            domain=contract.domain,
            kind=contract.kind,
            body=consolidated_body,
            pinned_pointers=current_ver.pinned_pointers,
            change_kind="breaking",
            changed_elements=changed_elements,
            change_summary="; ".join(r.reason for r in pending),
            approved_by=dec.decided_by or f"lead:{contract.domain}",
        )

        # Evaluate subscribed tasks by overlap and lifecycle state
        subs = await self._repo.list_contract_subscriptions(contract_id)
        reopened_ids: list[str] = []
        buffered_ids: list[str] = []
        delta_ids: list[str] = []
        freeze_candidates: list[TaskRecord] = []

        for sub in subs:
            if not _scope_overlaps(sub.scope, changed_elements):
                continue
            task = await self._repo.get_task(sub.task_id)
            if task.status in {TaskStatus.PENDING, TaskStatus.BLOCKED, TaskStatus.READY}:
                await self._repo.emit_event(
                    run_id=contract.run_id,
                    event_type="contract.subscriber_updated",
                    subject_id=task.id,
                    payload={
                        "task_id": task.id,
                        "contract_id": contract_id,
                        "version": new_ver.version,
                        "changed_elements": changed_elements,
                        "reopened": False,
                    },
                )
            elif task.status in {TaskStatus.MERGED, TaskStatus.DEPLOYED}:
                # Never reopen MERGED/DEPLOYED tasks: spawn a fresh delta task from main
                write_set = await self._repo.get_task_write_sets(task.id)
                delta = await self._repo.create_task(
                    run_id=contract.run_id,
                    wave=task.wave,
                    domain=task.domain,
                    feature=task.feature,
                    kind="delta",
                    title=(
                        f"Adapt {task.feature or task.domain} to {contract.name} v{new_ver.version}"
                    ),
                    description=(
                        f"Contract '{contract.name}' updated to v{new_ver.version} "
                        f"(changed={changed_elements}); adapt previously merged task {task.id}."
                    ),
                    status=TaskStatus.READY,
                    write_set=write_set,
                    parent_task_id=task.id,
                    budget_implement_usd=task.budget_implement_usd,
                    budget_review_usd=task.budget_review_usd,
                )
                delta_ids.append(delta.id)
            elif task.status in {
                TaskStatus.IN_PROGRESS,
                TaskStatus.IN_REVIEW,
                TaskStatus.VERIFIED,
                TaskStatus.QUEUED,
            }:
                if task.contract_reopens >= self.max_reopens_per_task:
                    freeze_candidates.append(task)
                elif task.status == TaskStatus.IN_PROGRESS:
                    await self._repo.increment_task_contract_reopens(task.id, task.version)
                    self._buffered_diffs[task.id].append(
                        {
                            "contract_id": contract_id,
                            "name": contract.name,
                            "version": new_ver.version,
                            "changed_elements": changed_elements,
                            "body": consolidated_body,
                        }
                    )
                    buffered_ids.append(task.id)
                else:
                    reopened = await self._repo.increment_task_contract_reopens(
                        task.id, task.version
                    )
                    await self._repo.set_task_status(
                        task.id,
                        TaskStatus.READY,
                        reopened.version,
                        event_payload={
                            "reopened_by_contract": contract.name,
                            "contract_version": new_ver.version,
                        },
                    )
                    reopened_ids.append(task.id)

        frozen_ids: list[str] = []
        contract_frozen = False
        if freeze_candidates:
            frozen_ids = await self._trigger_freeze_and_codesign(
                contract=updated_ctr,
                current_body=consolidated_body,
                pinned_pointers=current_ver.pinned_pointers,
                requests=pending,
                candidates=freeze_candidates,
            )
            contract_frozen = True

        return ContractBatchResult(
            contract_id=contract_id,
            batch_id=batch_id,
            approved=True,
            new_version=new_ver.version,
            contract_frozen=contract_frozen,
            reopened_task_ids=reopened_ids,
            buffered_task_ids=buffered_ids,
            frozen_task_ids=frozen_ids,
            delta_task_ids=delta_ids,
            decision_id=dec.id,
            reason="approved",
        )

    async def _trigger_freeze_and_codesign(
        self,
        *,
        contract: ContractRecord,
        current_body: dict[str, Any],
        pinned_pointers: Sequence[str],
        requests: Sequence[ContractChangeRequestRecord],
        candidates: Sequence[TaskRecord],
    ) -> list[str]:
        """Freeze livelocked tasks + contract, run one joint co-design call, and unfreeze READY."""
        frozen_tasks: list[TaskRecord] = []
        for t in candidates:
            ft = await self._repo.set_task_status(
                t.id,
                TaskStatus.FROZEN,
                t.version,
                event_payload={"reason": "contract_reopen_cap"},
            )
            frozen_tasks.append(ft)

        if self._co_design_handler is not None:
            final_body = await self._co_design_handler.co_design_freeze(
                contract=contract,
                current_body=current_body,
                requests=requests,
                frozen_tasks=frozen_tasks,
            )
        else:
            final_body = dict(current_body)

        locked_ctr, _ = await self._repo.save_contract_version(
            run_id=contract.run_id,
            name=contract.name,
            domain=contract.domain,
            kind=contract.kind,
            body=final_body,
            pinned_pointers=pinned_pointers,
            change_kind="additive",
            change_summary="Joint Lead co-design resolution locking wave contract",
            approved_by="joint_lead_codesign",
        )
        await self._repo.lock_contract(locked_ctr.id, locked_ctr.version, status="frozen")

        for ft in frozen_tasks:
            await self._repo.set_task_status(
                ft.id,
                TaskStatus.READY,
                ft.version,
                event_payload={"unfrozen_after_codesign": True},
            )
        return [ft.id for ft in frozen_tasks]
