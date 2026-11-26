"""BlackboardRepository Protocol, Postgres implementation (outbox + locks), and test stores."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from ulid import ULID

from foundry.blackboard.db import BlackboardDB
from foundry.blackboard.models import (
    AgentRecord,
    AgentRow,
    BlackboardEvent,
    ContractChangeRequestRecord,
    ContractChangeRequestRow,
    ContractRecord,
    ContractRow,
    ContractSubscriptionRecord,
    ContractSubscriptionRow,
    ContractVersionRecord,
    ContractVersionRow,
    DecisionRecord,
    DecisionRow,
    EventRow,
    FeatureRecord,
    FeatureRow,
    LlmCallRecord,
    LlmCallRow,
    MessageRecord,
    MessageRow,
    NoteRecord,
    NoteRow,
    ProcessedEventRow,
    ReservationConflict,
    RunRecord,
    RunRow,
    TaskDependencyRow,
    TaskRecord,
    TaskRow,
    TaskStatus,
    TaskWriteSetRow,
    ThreadRow,
    WriteReservationRecord,
    WriteReservationRow,
)
from foundry.security.workspace_guard import normalize_repo_path, paths_overlap


class StaleWriteError(Exception):
    """Raised when an optimistic locking version check fails (`version != expected_version`)."""


class NotFoundError(Exception):
    """Raised when a requested Blackboard entity does not exist."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{str(ULID()).lower()}"


def _rowcount(res: Any) -> int:
    return int(cast(CursorResult[Any], res).rowcount or 0)


def _to_run_dto(row: RunRow) -> RunRecord:
    return RunRecord(
        id=row.id,
        project_id=row.project_id,
        phase=row.phase,
        budget_usd=float(row.budget_usd),
        spent_usd=float(row.spent_usd),
        data_policy=dict(row.data_policy),
        started_at=row.started_at,
        ended_at=row.ended_at,
    )


def _to_agent_dto(row: AgentRow) -> AgentRecord:
    return AgentRecord(
        id=row.id,
        run_id=row.run_id,
        role=row.role,
        domain=row.domain,
        parent_agent_id=row.parent_agent_id,
        model_profile=row.model_profile,
        status=row.status,
        graph_thread_id=row.graph_thread_id,
    )


def _to_task_dto(row: TaskRow) -> TaskRecord:
    return TaskRecord(
        id=row.id,
        run_id=row.run_id,
        wave=int(row.wave),
        domain=row.domain,
        feature=row.feature,
        kind=row.kind,
        title=row.title,
        description=row.description,
        acceptance=list(row.acceptance),
        status=TaskStatus(row.status),
        owner_agent_id=row.owner_agent_id,
        parent_task_id=row.parent_task_id,
        attempt=int(row.attempt),
        max_attempts=int(row.max_attempts),
        contract_reopens=int(row.contract_reopens),
        base_sha=row.base_sha,
        branch=row.branch,
        worktree_path=row.worktree_path,
        graph_thread_id=row.graph_thread_id,
        pending_deps=list(row.pending_deps),
        budget_implement_usd=float(row.budget_implement_usd),
        budget_review_usd=float(row.budget_review_usd),
        spent_implement_usd=float(row.spent_implement_usd),
        spent_review_usd=float(row.spent_review_usd),
        version=int(row.version),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_contract_dto(row: ContractRow) -> ContractRecord:
    return ContractRecord(
        id=row.id,
        run_id=row.run_id,
        name=row.name,
        domain=row.domain,
        kind=row.kind,
        owner_agent_id=row.owner_agent_id,
        current_version=int(row.current_version),
        status=row.status,
        breaking_in_wave=int(row.breaking_in_wave),
        version=int(row.version),
    )


def _to_cv_dto(row: ContractVersionRow) -> ContractVersionRecord:
    return ContractVersionRecord(
        contract_id=row.contract_id,
        version=int(row.version),
        body=dict(row.body),
        pinned_pointers=list(row.pinned_pointers),
        change_kind=row.change_kind,
        changed_elements=list(row.changed_elements),
        change_summary=row.change_summary,
        author_agent_id=row.author_agent_id,
        approved_by=row.approved_by,
        created_at=row.created_at,
    )


def _to_ccr_dto(row: ContractChangeRequestRow) -> ContractChangeRequestRecord:
    return ContractChangeRequestRecord(
        id=row.id,
        contract_id=row.contract_id,
        requested_by=row.requested_by,
        proposed_body=dict(row.proposed_body),
        reason=row.reason,
        classified_as=row.classified_as,
        status=row.status,
        batch_id=row.batch_id,
        created_at=row.created_at,
    )


def _to_decision_dto(row: DecisionRow) -> DecisionRecord:
    return DecisionRecord(
        id=row.id,
        run_id=row.run_id,
        idempotency_key=row.idempotency_key,
        category=row.category,
        title=row.title,
        context=row.context,
        options=list(row.options),
        recommendation=row.recommendation,
        confidence=row.confidence,
        reversibility=row.reversibility,
        impact=dict(row.impact),
        mode=row.mode,
        status=row.status,
        answer=row.answer,
        decided_by=row.decided_by,
        graph_thread_id=row.graph_thread_id,
        blocks_task_ids=list(row.blocks_task_ids),
        version=int(row.version),
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


def _to_event_dto(row: EventRow) -> BlackboardEvent:
    return BlackboardEvent(
        id=int(row.id),
        publish_seq=int(row.publish_seq) if row.publish_seq is not None else None,
        run_id=row.run_id,
        type=row.type,
        subject_id=row.subject_id,
        payload=dict(row.payload),
        created_at=row.created_at,
        published_at=row.published_at,
    )


def _to_reservation_dto(row: WriteReservationRow) -> WriteReservationRecord:
    return WriteReservationRecord(
        id=int(row.id),
        run_id=row.run_id,
        task_id=row.task_id,
        path=row.path,
        expires_at=row.expires_at,
        heartbeat_at=row.heartbeat_at,
        released_at=row.released_at,
    )


def _to_feature_dto(row: FeatureRow) -> FeatureRecord:
    return FeatureRecord(
        run_id=row.run_id,
        name=row.name,
        status=row.status,
        budget_blackbox_tests_usd=float(row.budget_blackbox_tests_usd),
        spent_blackbox_tests_usd=float(row.spent_blackbox_tests_usd),
        dispute_rounds=int(row.dispute_rounds),
        updated_at=row.updated_at,
    )


def _to_llm_call_dto(row: LlmCallRow) -> LlmCallRecord:
    return LlmCallRecord(
        id=int(row.id),
        run_id=row.run_id,
        agent_id=row.agent_id,
        task_id=row.task_id,
        graph_thread_id=row.graph_thread_id,
        node=row.node,
        provider=row.provider,
        model=row.model,
        profile=row.profile,
        transport=row.transport,
        billing_mode=row.billing_mode,
        input_tokens=int(row.input_tokens),
        cached_tokens=int(row.cached_tokens),
        output_tokens=int(row.output_tokens),
        cost_usd=float(row.cost_usd),
        est_value_usd=float(row.est_value_usd),
        latency_ms=row.latency_ms,
        replayed=bool(row.replayed),
        prompt_hash=row.prompt_hash,
        created_at=row.created_at,
    )


@runtime_checkable
class BlackboardRepository(Protocol):
    """Typed storage + transactional outbox contract for Foundry Blackboard state."""

    async def create_run(
        self,
        *,
        project_id: str,
        budget_usd: float,
        data_policy: dict[str, Any] | None = None,
        run_id: str | None = None,
        phase: str = "planning",
    ) -> RunRecord: ...

    async def get_run(self, run_id: str) -> RunRecord: ...

    async def list_runs(self, project_id: str | None = None) -> list[RunRecord]: ...

    async def update_run_phase(
        self,
        run_id: str,
        phase: str,
        *,
        spent_usd: float | None = None,
        ended: bool = False,
    ) -> RunRecord: ...

    async def register_agent(
        self,
        *,
        run_id: str,
        role: str,
        model_profile: str,
        domain: str | None = None,
        parent_agent_id: str | None = None,
        graph_thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> AgentRecord: ...

    async def update_agent_status(self, agent_id: str, status: str) -> AgentRecord: ...

    async def list_agents(self, run_id: str) -> list[AgentRecord]: ...

    async def get_agent(self, agent_id: str) -> AgentRecord: ...

    async def create_task(
        self,
        *,
        run_id: str,
        wave: int,
        domain: str,
        title: str,
        description: str,
        budget_implement_usd: float,
        budget_review_usd: float,
        feature: str | None = None,
        kind: str = "build",
        status: TaskStatus = TaskStatus.PENDING,
        acceptance: Sequence[Any] = (),
        write_set: Sequence[str] = (),
        depends_on_task_ids: Sequence[str] = (),
        task_id: str | None = None,
        base_sha: str | None = None,
        graph_thread_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> TaskRecord: ...

    async def get_task(self, task_id: str) -> TaskRecord: ...

    async def list_tasks(
        self,
        run_id: str,
        *,
        wave: int | None = None,
        status: TaskStatus | None = None,
    ) -> list[TaskRecord]: ...

    async def get_task_write_sets(self, task_id: str) -> list[str]: ...

    async def get_task_dependencies(self, task_id: str) -> list[str]: ...

    async def set_task_status(
        self,
        task_id: str,
        new_status: TaskStatus,
        expected_version: int,
        *,
        attempt: int | None = None,
        branch: str | None = None,
        base_sha: str | None = None,
        owner_agent_id: str | None = None,
        graph_thread_id: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> TaskRecord: ...

    async def increment_task_contract_reopens(
        self, task_id: str, expected_version: int
    ) -> TaskRecord: ...

    async def update_task_pending_deps(
        self,
        task_id: str,
        pending_deps: Sequence[dict[str, Any]],
        expected_version: int,
    ) -> TaskRecord: ...

    async def update_task_metadata(
        self,
        task_id: str,
        expected_version: int,
        *,
        base_sha: str | None = None,
        branch: str | None = None,
        worktree_path: str | None = None,
        attempt: int | None = None,
        graph_thread_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> TaskRecord: ...

    async def increment_task_review_spend(self, task_id: str, delta_usd: float) -> TaskRecord: ...

    async def acquire_write_reservations(
        self,
        run_id: str,
        task_id: str,
        paths: Sequence[str],
        *,
        ttl_seconds: int = 600,
    ) -> tuple[list[WriteReservationRecord], list[ReservationConflict]]: ...

    async def renew_write_reservations(
        self,
        run_id: str,
        *,
        task_ids: Sequence[str] | None = None,
        ttl_seconds: int = 600,
    ) -> int: ...

    async def release_write_reservations(
        self,
        run_id: str,
        task_id: str,
        *,
        reason: str = "released",
    ) -> int: ...

    async def list_active_reservations(
        self,
        run_id: str,
        *,
        include_expired: bool = False,
    ) -> list[WriteReservationRecord]: ...

    async def save_contract_version(
        self,
        *,
        run_id: str,
        name: str,
        domain: str,
        kind: str,
        body: dict[str, Any],
        author_agent_id: str | None = None,
        pinned_pointers: Sequence[str] = (),
        change_kind: str = "initial",
        changed_elements: Sequence[str] = (),
        change_summary: str | None = None,
        approved_by: str | None = None,
    ) -> tuple[ContractRecord, ContractVersionRecord]: ...

    async def get_contract(self, run_id: str, name: str) -> ContractRecord: ...

    async def get_contract_by_id(self, contract_id: str) -> ContractRecord: ...

    async def list_contracts(self, run_id: str) -> list[ContractRecord]: ...

    async def get_latest_contract_version(self, contract_id: str) -> ContractVersionRecord: ...

    async def list_contract_versions(self, contract_id: str) -> list[ContractVersionRecord]: ...

    async def lock_contract(
        self,
        contract_id: str,
        expected_version: int,
        *,
        status: str = "locked",
    ) -> ContractRecord: ...

    async def create_contract_change_request(
        self,
        *,
        contract_id: str,
        requested_by: str,
        proposed_body: dict[str, Any],
        reason: str,
        classified_as: str,
        status: str = "pending",
    ) -> ContractChangeRequestRecord: ...

    async def list_contract_change_requests(
        self, contract_id: str
    ) -> list[ContractChangeRequestRecord]: ...

    async def subscribe_task_to_contract(
        self,
        contract_id: str,
        task_id: str,
        scope: Sequence[str] = (),
    ) -> ContractSubscriptionRecord: ...

    async def list_contract_subscriptions(
        self, contract_id: str
    ) -> list[ContractSubscriptionRecord]: ...

    async def update_contract_change_request(
        self,
        request_id: str,
        *,
        status: str,
        batch_id: str | None = None,
        classified_as: str | None = None,
    ) -> ContractChangeRequestRecord: ...

    async def increment_contract_breaking_in_wave(
        self, contract_id: str, expected_version: int
    ) -> ContractRecord: ...

    async def upsert_feature(
        self,
        run_id: str,
        name: str,
        *,
        status: str = "pending",
        budget_blackbox_tests_usd: float = 0.30,
        spent_delta_usd: float = 0.0,
        dispute_rounds_delta: int = 0,
    ) -> FeatureRecord: ...

    async def list_features(self, run_id: str) -> list[FeatureRecord]: ...

    async def get_feature(self, run_id: str, name: str) -> FeatureRecord | None: ...

    async def get_or_create_decision(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        category: str,
        title: str,
        context: str,
        options: Sequence[Any],
        mode: str,
        status: str = "pending",
        recommendation: Any | None = None,
        confidence: float | None = None,
        reversibility: str | None = "reversible",
        impact: dict[str, Any] | None = None,
        answer: Any | None = None,
        decided_by: str | None = None,
        graph_thread_id: str | None = None,
        blocks_task_ids: Sequence[str] = (),
    ) -> tuple[DecisionRecord, bool]: ...

    async def get_decision(self, decision_id: str) -> DecisionRecord: ...

    async def list_decisions(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
    ) -> list[DecisionRecord]: ...

    async def resolve_decision(
        self,
        decision_id: str,
        expected_version: int,
        *,
        status: str,
        answer: Any,
        decided_by: str,
    ) -> DecisionRecord: ...

    async def post_note(
        self,
        *,
        run_id: str,
        kind: str,
        body: str,
        task_id: str | None = None,
        feature: str | None = None,
    ) -> NoteRecord: ...

    async def list_notes(self, run_id: str, *, feature: str | None = None) -> list[NoteRecord]: ...

    async def ensure_thread(
        self,
        *,
        run_id: str,
        kind: str,
        subject_id: str | None = None,
        thread_id: str | None = None,
    ) -> str: ...

    async def post_message(
        self,
        *,
        thread_id: str,
        from_id: str,
        body: str,
        to_id: str | None = None,
    ) -> MessageRecord: ...

    async def list_messages(self, thread_id: str) -> list[MessageRecord]: ...

    async def record_llm_call(
        self,
        *,
        run_id: str | None,
        provider: str,
        model: str,
        profile: str,
        transport: str,
        billing_mode: str,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        cost_usd: float,
        est_value_usd: float,
        agent_id: str | None = None,
        task_id: str | None = None,
        graph_thread_id: str | None = None,
        node: str | None = None,
        latency_ms: int | None = None,
        replayed: bool = False,
        prompt_hash: str | None = None,
    ) -> LlmCallRecord: ...

    async def list_llm_calls(self, run_id: str) -> list[LlmCallRecord]: ...

    async def emit_event(
        self,
        *,
        run_id: str,
        event_type: str,
        subject_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> BlackboardEvent: ...

    async def publish_pending_events(self, limit: int = 500) -> list[BlackboardEvent]: ...

    async def mark_event_processed(self, consumer_group: str, publish_seq: int) -> bool: ...

    async def last_processed_seq(self, consumer_group: str) -> int: ...

    async def list_events_after(
        self,
        after_seq: int = 0,
        *,
        run_id: str | None = None,
    ) -> list[BlackboardEvent]: ...


class PostgresBlackboardRepository:
    """Async SQLAlchemy 2 + asyncpg Blackboard repository with transactional outbox."""

    def __init__(self, db: BlackboardDB) -> None:
        self._db = db

    async def create_run(
        self,
        *,
        project_id: str,
        budget_usd: float,
        data_policy: dict[str, Any] | None = None,
        run_id: str | None = None,
        phase: str = "planning",
    ) -> RunRecord:
        rid = run_id or _new_id("run")
        async with self._db.session_factory() as s, s.begin():
            row = RunRow(
                id=rid,
                project_id=project_id,
                phase=phase,
                budget_usd=budget_usd,
                spent_usd=0.0,
                data_policy=dict(
                    data_policy or {"allow_free_tier": False, "allow_trace_export": False}
                ),
            )
            s.add(row)
            s.add(
                EventRow(
                    run_id=rid,
                    type="run.created",
                    subject_id=rid,
                    payload={"run_id": rid, "project_id": project_id, "phase": phase},
                )
            )
            await s.flush()
            await s.refresh(row)
            return _to_run_dto(row)

    async def get_run(self, run_id: str) -> RunRecord:
        async with self._db.session_factory() as s:
            row = await s.get(RunRow, run_id)
            if row is None:
                raise NotFoundError(f"Run not found: {run_id}")
            return _to_run_dto(row)

    async def list_runs(self, project_id: str | None = None) -> list[RunRecord]:
        async with self._db.session_factory() as s:
            stmt = select(RunRow)
            if project_id is not None:
                stmt = stmt.where(RunRow.project_id == project_id)
            stmt = stmt.order_by(RunRow.started_at.desc(), RunRow.id.desc())
            rows = (await s.scalars(stmt)).all()
            return [_to_run_dto(r) for r in rows]

    async def update_run_phase(
        self,
        run_id: str,
        phase: str,
        *,
        spent_usd: float | None = None,
        ended: bool = False,
    ) -> RunRecord:
        async with self._db.session_factory() as s, s.begin():
            values: dict[str, Any] = {"phase": phase}
            if spent_usd is not None:
                values["spent_usd"] = spent_usd
            if ended:
                values["ended_at"] = datetime.now(UTC)
            res = await s.execute(update(RunRow).where(RunRow.id == run_id).values(**values))
            if _rowcount(res) == 0:
                raise NotFoundError(f"Run not found: {run_id}")
            s.add(
                EventRow(
                    run_id=run_id,
                    type="run.phase_changed",
                    subject_id=run_id,
                    payload={"run_id": run_id, "phase": phase},
                )
            )
            row = await s.get(RunRow, run_id)
            assert row is not None
            return _to_run_dto(row)

    async def register_agent(
        self,
        *,
        run_id: str,
        role: str,
        model_profile: str,
        domain: str | None = None,
        parent_agent_id: str | None = None,
        graph_thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> AgentRecord:
        aid = agent_id or _new_id("agt")
        async with self._db.session_factory() as s, s.begin():
            row = AgentRow(
                id=aid,
                run_id=run_id,
                role=role,
                domain=domain,
                parent_agent_id=parent_agent_id,
                model_profile=model_profile,
                status="idle",
                graph_thread_id=graph_thread_id,
            )
            s.add(row)
            await s.flush()
            return _to_agent_dto(row)

    async def update_agent_status(self, agent_id: str, status: str) -> AgentRecord:
        async with self._db.session_factory() as s, s.begin():
            row = await s.get(AgentRow, agent_id)
            if row is None:
                raise NotFoundError(f"Agent not found: {agent_id}")
            row.status = status
            await s.flush()
            return _to_agent_dto(row)

    async def list_agents(self, run_id: str) -> list[AgentRecord]:
        async with self._db.session_factory() as s:
            rows = (
                await s.scalars(
                    select(AgentRow).where(AgentRow.run_id == run_id).order_by(AgentRow.id)
                )
            ).all()
            return [_to_agent_dto(r) for r in rows]

    async def get_agent(self, agent_id: str) -> AgentRecord:
        async with self._db.session_factory() as s:
            row = await s.get(AgentRow, agent_id)
            if row is None:
                raise NotFoundError(f"Agent not found: {agent_id}")
            return _to_agent_dto(row)

    async def create_task(
        self,
        *,
        run_id: str,
        wave: int,
        domain: str,
        title: str,
        description: str,
        budget_implement_usd: float,
        budget_review_usd: float,
        feature: str | None = None,
        kind: str = "build",
        status: TaskStatus = TaskStatus.PENDING,
        acceptance: Sequence[Any] = (),
        write_set: Sequence[str] = (),
        depends_on_task_ids: Sequence[str] = (),
        task_id: str | None = None,
        base_sha: str | None = None,
        graph_thread_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> TaskRecord:
        tid = task_id or _new_id("t")
        async with self._db.session_factory() as s, s.begin():
            row = TaskRow(
                id=tid,
                run_id=run_id,
                wave=wave,
                domain=domain,
                feature=feature,
                kind=kind,
                title=title,
                description=description,
                acceptance=list(acceptance),
                status=status,
                parent_task_id=parent_task_id,
                base_sha=base_sha,
                graph_thread_id=graph_thread_id,
                budget_implement_usd=budget_implement_usd,
                budget_review_usd=budget_review_usd,
            )
            s.add(row)
            await s.flush()

            for dep_id in depends_on_task_ids:
                s.add(TaskDependencyRow(task_id=tid, depends_on_task_id=dep_id, kind="blocks"))
            for path in write_set:
                s.add(TaskWriteSetRow(task_id=tid, path=path, mode="write", source="planned"))

            s.add(
                EventRow(
                    run_id=run_id,
                    type=f"task.{status.value.lower()}",
                    subject_id=tid,
                    payload={"task_id": tid, "wave": wave, "domain": domain, "kind": kind},
                )
            )
            await s.flush()
            await s.refresh(row)
            return _to_task_dto(row)

    async def get_task(self, task_id: str) -> TaskRecord:
        async with self._db.session_factory() as s:
            row = await s.get(TaskRow, task_id)
            if row is None:
                raise NotFoundError(f"Task not found: {task_id}")
            return _to_task_dto(row)

    async def list_tasks(
        self,
        run_id: str,
        *,
        wave: int | None = None,
        status: TaskStatus | None = None,
    ) -> list[TaskRecord]:
        async with self._db.session_factory() as s:
            stmt = select(TaskRow).where(TaskRow.run_id == run_id)
            if wave is not None:
                stmt = stmt.where(TaskRow.wave == wave)
            if status is not None:
                stmt = stmt.where(TaskRow.status == status)
            stmt = stmt.order_by(TaskRow.wave, TaskRow.created_at, TaskRow.id)
            rows = (await s.scalars(stmt)).all()
            return [_to_task_dto(r) for r in rows]

    async def get_task_write_sets(self, task_id: str) -> list[str]:
        async with self._db.session_factory() as s:
            rows = (
                await s.scalars(
                    select(TaskWriteSetRow.path).where(
                        TaskWriteSetRow.task_id == task_id,
                        TaskWriteSetRow.mode == "write",
                    )
                )
            ).all()
            return list(rows)

    async def get_task_dependencies(self, task_id: str) -> list[str]:
        async with self._db.session_factory() as s:
            rows = (
                await s.scalars(
                    select(TaskDependencyRow.depends_on_task_id).where(
                        TaskDependencyRow.task_id == task_id
                    )
                )
            ).all()
            return list(rows)

    async def set_task_status(
        self,
        task_id: str,
        new_status: TaskStatus,
        expected_version: int,
        *,
        attempt: int | None = None,
        branch: str | None = None,
        base_sha: str | None = None,
        owner_agent_id: str | None = None,
        graph_thread_id: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> TaskRecord:
        async with self._db.session_factory() as s, s.begin():
            existing = await s.get(TaskRow, task_id)
            if existing is None:
                raise NotFoundError(f"Task not found: {task_id}")
            values: dict[str, Any] = {
                "status": new_status,
                "version": TaskRow.version + 1,
                "updated_at": datetime.now(UTC),
            }
            if attempt is not None:
                values["attempt"] = attempt
            if branch is not None:
                values["branch"] = branch
            if base_sha is not None:
                values["base_sha"] = base_sha
            if owner_agent_id is not None:
                values["owner_agent_id"] = owner_agent_id
            if graph_thread_id is not None:
                values["graph_thread_id"] = graph_thread_id

            res = await s.execute(
                update(TaskRow)
                .where(TaskRow.id == task_id, TaskRow.version == expected_version)
                .values(**values)
            )
            if _rowcount(res) == 0:
                raise StaleWriteError(
                    f"Stale write on task {task_id} (expected_version={expected_version})"
                )

            payload = {"task_id": task_id, "status": new_status.value, **(event_payload or {})}
            s.add(
                EventRow(
                    run_id=existing.run_id,
                    type=f"task.{new_status.value.lower()}",
                    subject_id=task_id,
                    payload=payload,
                )
            )
            await s.flush()
            s.expire(existing)
            refreshed = await s.get(TaskRow, task_id)
            assert refreshed is not None
            return _to_task_dto(refreshed)

    async def increment_task_contract_reopens(
        self, task_id: str, expected_version: int
    ) -> TaskRecord:
        async with self._db.session_factory() as s, s.begin():
            existing = await s.get(TaskRow, task_id)
            if existing is None:
                raise NotFoundError(f"Task not found: {task_id}")
            res = await s.execute(
                update(TaskRow)
                .where(TaskRow.id == task_id, TaskRow.version == expected_version)
                .values(
                    contract_reopens=TaskRow.contract_reopens + 1,
                    version=TaskRow.version + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            if _rowcount(res) == 0:
                raise StaleWriteError(f"Stale write incrementing reopens on task {task_id}")
            s.expire(existing)
            refreshed = await s.get(TaskRow, task_id)
            assert refreshed is not None
            return _to_task_dto(refreshed)

    async def update_task_pending_deps(
        self,
        task_id: str,
        pending_deps: Sequence[dict[str, Any]],
        expected_version: int,
    ) -> TaskRecord:
        async with self._db.session_factory() as s, s.begin():
            existing = await s.get(TaskRow, task_id)
            if existing is None:
                raise NotFoundError(f"Task not found: {task_id}")
            res = await s.execute(
                update(TaskRow)
                .where(TaskRow.id == task_id, TaskRow.version == expected_version)
                .values(
                    pending_deps=[dict(item) for item in pending_deps],
                    version=TaskRow.version + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            if _rowcount(res) == 0:
                raise StaleWriteError(f"Stale write updating pending_deps on task {task_id}")
            s.expire(existing)
            refreshed = await s.get(TaskRow, task_id)
            assert refreshed is not None
            return _to_task_dto(refreshed)

    async def update_task_metadata(
        self,
        task_id: str,
        expected_version: int,
        *,
        base_sha: str | None = None,
        branch: str | None = None,
        worktree_path: str | None = None,
        attempt: int | None = None,
        graph_thread_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> TaskRecord:
        async with self._db.session_factory() as s, s.begin():
            existing = await s.get(TaskRow, task_id)
            if existing is None:
                raise NotFoundError(f"Task not found: {task_id}")
            values: dict[str, Any] = {
                "version": TaskRow.version + 1,
                "updated_at": datetime.now(UTC),
            }
            if base_sha is not None:
                values["base_sha"] = base_sha
            if branch is not None:
                values["branch"] = branch
            if worktree_path is not None:
                values["worktree_path"] = worktree_path
            if attempt is not None:
                values["attempt"] = attempt
            if graph_thread_id is not None:
                values["graph_thread_id"] = graph_thread_id
            if parent_task_id is not None:
                values["parent_task_id"] = parent_task_id
            res = await s.execute(
                update(TaskRow)
                .where(TaskRow.id == task_id, TaskRow.version == expected_version)
                .values(**values)
            )
            if _rowcount(res) == 0:
                raise StaleWriteError(f"Stale write updating metadata on task {task_id}")
            s.expire(existing)
            refreshed = await s.get(TaskRow, task_id)
            assert refreshed is not None
            return _to_task_dto(refreshed)

    async def increment_task_review_spend(self, task_id: str, delta_usd: float) -> TaskRecord:
        async with self._db.session_factory() as s, s.begin():
            row = await s.get(TaskRow, task_id)
            if row is None:
                raise NotFoundError(f"Task not found: {task_id}")
            row.spent_review_usd = float(row.spent_review_usd) + float(delta_usd)
            row.updated_at = datetime.now(UTC)
            run_row = await s.get(RunRow, row.run_id)
            if run_row is not None:
                run_row.spent_usd = float(run_row.spent_usd) + float(delta_usd)
            await s.flush()
            await s.refresh(row)
            return _to_task_dto(row)

    async def acquire_write_reservations(
        self,
        run_id: str,
        task_id: str,
        paths: Sequence[str],
        *,
        ttl_seconds: int = 600,
    ) -> tuple[list[WriteReservationRecord], list[ReservationConflict]]:
        norm_paths = list(
            dict.fromkeys(normalize_repo_path(p, allow_directory_slash=True) for p in paths)
        )
        if not norm_paths:
            return [], []
        async with self._db.session_factory() as s, s.begin():
            await s.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('reserve:' || :run_id))"),
                {"run_id": run_id},
            )
            conflict_res = await s.execute(
                text(
                    """
                    SELECT r.task_id, r.path AS holder_path, p.path AS requested_path
                    FROM write_reservations r
                    JOIN unnest(CAST(:paths AS text[])) AS p(path)
                      ON r.path = p.path
                      OR rtrim(r.path, '/') = rtrim(p.path, '/')
                      OR (right(r.path, 1) = '/' AND starts_with(p.path, r.path))
                      OR (right(p.path, 1) = '/' AND starts_with(r.path, p.path))
                    WHERE r.run_id = :run_id
                      AND r.task_id <> :task_id
                      AND r.released_at IS NULL
                      AND r.expires_at > now()
                    """
                ),
                {"run_id": run_id, "task_id": task_id, "paths": norm_paths},
            )
            conflict_rows = conflict_res.mappings().all()
            if conflict_rows:
                conflicts = [
                    ReservationConflict(
                        requested_path=str(r["requested_path"]),
                        holder_task_id=str(r["task_id"]),
                        holder_path=str(r["holder_path"]),
                    )
                    for r in conflict_rows
                ]
                s.add(
                    EventRow(
                        run_id=run_id,
                        type="reservation.denied",
                        subject_id=task_id,
                        payload={
                            "task_id": task_id,
                            "conflicts": [c.model_dump(mode="json") for c in conflicts],
                        },
                    )
                )
                return [], conflicts

            now = datetime.now(UTC)
            expires = now + timedelta(seconds=ttl_seconds)
            existing_rows = (
                await s.scalars(
                    select(WriteReservationRow).where(
                        WriteReservationRow.run_id == run_id,
                        WriteReservationRow.task_id == task_id,
                        WriteReservationRow.released_at.is_(None),
                    )
                )
            ).all()
            existing_by_path = {row.path: row for row in existing_rows}
            created_or_renewed: list[WriteReservationRow] = []
            for path in norm_paths:
                existing_row = existing_by_path.get(path)
                if existing_row is not None:
                    existing_row.expires_at = expires
                    existing_row.heartbeat_at = now
                    created_or_renewed.append(existing_row)
                else:
                    row = WriteReservationRow(
                        run_id=run_id,
                        task_id=task_id,
                        path=path,
                        expires_at=expires,
                        heartbeat_at=now,
                    )
                    s.add(row)
                    created_or_renewed.append(row)
            s.add(
                EventRow(
                    run_id=run_id,
                    type="reservation.granted",
                    subject_id=task_id,
                    payload={"task_id": task_id, "paths": norm_paths},
                )
            )
            await s.flush()
            for row in created_or_renewed:
                await s.refresh(row)
            return [_to_reservation_dto(r) for r in created_or_renewed], []

    async def renew_write_reservations(
        self,
        run_id: str,
        *,
        task_ids: Sequence[str] | None = None,
        ttl_seconds: int = 600,
    ) -> int:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl_seconds)
        async with self._db.session_factory() as s, s.begin():
            stmt = (
                update(WriteReservationRow)
                .where(
                    WriteReservationRow.run_id == run_id,
                    WriteReservationRow.released_at.is_(None),
                    WriteReservationRow.expires_at > now,
                )
                .values(expires_at=expires, heartbeat_at=now)
            )
            if task_ids is not None:
                id_list = list(task_ids)
                if not id_list:
                    return 0
                stmt = stmt.where(WriteReservationRow.task_id.in_(id_list))
            res = await s.execute(stmt)
            return _rowcount(res)

    async def release_write_reservations(
        self,
        run_id: str,
        task_id: str,
        *,
        reason: str = "released",
    ) -> int:
        now = datetime.now(UTC)
        async with self._db.session_factory() as s, s.begin():
            res = await s.execute(
                update(WriteReservationRow)
                .where(
                    WriteReservationRow.run_id == run_id,
                    WriteReservationRow.task_id == task_id,
                    WriteReservationRow.released_at.is_(None),
                )
                .values(released_at=now)
            )
            count = _rowcount(res)
            if count > 0:
                ev_type = "reservation.expired" if reason == "expired" else "reservation.released"
                s.add(
                    EventRow(
                        run_id=run_id,
                        type=ev_type,
                        subject_id=task_id,
                        payload={"task_id": task_id, "count": count, "reason": reason},
                    )
                )
            return count

    async def list_active_reservations(
        self,
        run_id: str,
        *,
        include_expired: bool = False,
    ) -> list[WriteReservationRecord]:
        async with self._db.session_factory() as s:
            stmt = select(WriteReservationRow).where(
                WriteReservationRow.run_id == run_id,
                WriteReservationRow.released_at.is_(None),
            )
            if not include_expired:
                stmt = stmt.where(WriteReservationRow.expires_at > datetime.now(UTC))
            stmt = stmt.order_by(WriteReservationRow.id)
            rows = (await s.scalars(stmt)).all()
            return [_to_reservation_dto(r) for r in rows]

    async def save_contract_version(
        self,
        *,
        run_id: str,
        name: str,
        domain: str,
        kind: str,
        body: dict[str, Any],
        author_agent_id: str | None = None,
        pinned_pointers: Sequence[str] = (),
        change_kind: str = "initial",
        changed_elements: Sequence[str] = (),
        change_summary: str | None = None,
        approved_by: str | None = None,
    ) -> tuple[ContractRecord, ContractVersionRecord]:
        async with self._db.session_factory() as s, s.begin():
            row = await s.scalar(
                select(ContractRow).where(ContractRow.run_id == run_id, ContractRow.name == name)
            )
            if row is None:
                cid = _new_id("ctr")
                row = ContractRow(
                    id=cid,
                    run_id=run_id,
                    name=name,
                    domain=domain,
                    kind=kind,
                    owner_agent_id=author_agent_id,
                    current_version=1,
                    status="draft",
                    breaking_in_wave=0,
                    version=1,
                )
                s.add(row)
                await s.flush()
                new_ver = 1
            else:
                new_ver = int(row.current_version) + 1
                row.current_version = new_ver
                row.version = int(row.version) + 1
                if change_kind == "breaking":
                    row.breaking_in_wave = int(row.breaking_in_wave) + 1
                await s.flush()

            ver_row = ContractVersionRow(
                contract_id=row.id,
                version=new_ver,
                body=dict(body),
                pinned_pointers=list(pinned_pointers),
                change_kind=change_kind,
                changed_elements=list(changed_elements),
                change_summary=change_summary,
                author_agent_id=author_agent_id,
                approved_by=approved_by,
            )
            s.add(ver_row)
            s.add(
                EventRow(
                    run_id=run_id,
                    type="contract.versioned",
                    subject_id=row.id,
                    payload={"contract_id": row.id, "name": name, "version": new_ver},
                )
            )
            await s.flush()
            await s.refresh(ver_row)
            return _to_contract_dto(row), _to_cv_dto(ver_row)

    async def get_contract(self, run_id: str, name: str) -> ContractRecord:
        async with self._db.session_factory() as s:
            row = await s.scalar(
                select(ContractRow).where(ContractRow.run_id == run_id, ContractRow.name == name)
            )
            if row is None:
                raise NotFoundError(f"Contract not found: {run_id}/{name}")
            return _to_contract_dto(row)

    async def get_contract_by_id(self, contract_id: str) -> ContractRecord:
        async with self._db.session_factory() as s:
            row = await s.get(ContractRow, contract_id)
            if row is None:
                raise NotFoundError(f"Contract not found: {contract_id}")
            return _to_contract_dto(row)

    async def list_contracts(self, run_id: str) -> list[ContractRecord]:
        async with self._db.session_factory() as s:
            rows = (
                await s.scalars(
                    select(ContractRow)
                    .where(ContractRow.run_id == run_id)
                    .order_by(ContractRow.name)
                )
            ).all()
            return [_to_contract_dto(r) for r in rows]

    async def get_latest_contract_version(self, contract_id: str) -> ContractVersionRecord:
        async with self._db.session_factory() as s:
            contract = await s.get(ContractRow, contract_id)
            if contract is None:
                raise NotFoundError(f"Contract not found: {contract_id}")
            ver_row = await s.get(ContractVersionRow, (contract_id, contract.current_version))
            if ver_row is None:
                raise NotFoundError(f"No versions for contract {contract_id}")
            return _to_cv_dto(ver_row)

    async def list_contract_versions(self, contract_id: str) -> list[ContractVersionRecord]:
        async with self._db.session_factory() as s:
            contract = await s.get(ContractRow, contract_id)
            if contract is None:
                raise NotFoundError(f"Contract not found: {contract_id}")
            rows = (
                await s.scalars(
                    select(ContractVersionRow)
                    .where(ContractVersionRow.contract_id == contract_id)
                    .order_by(ContractVersionRow.version)
                )
            ).all()
            return [_to_cv_dto(r) for r in rows]

    async def lock_contract(
        self,
        contract_id: str,
        expected_version: int,
        *,
        status: str = "locked",
    ) -> ContractRecord:
        async with self._db.session_factory() as s, s.begin():
            existing = await s.get(ContractRow, contract_id)
            if existing is None:
                raise NotFoundError(f"Contract not found: {contract_id}")
            res = await s.execute(
                update(ContractRow)
                .where(ContractRow.id == contract_id, ContractRow.version == expected_version)
                .values(status=status, version=ContractRow.version + 1)
            )
            if _rowcount(res) == 0:
                raise StaleWriteError(f"Stale write on contract {contract_id}")
            s.add(
                EventRow(
                    run_id=existing.run_id,
                    type=f"contract.{status}",
                    subject_id=contract_id,
                    payload={"contract_id": contract_id, "name": existing.name, "status": status},
                )
            )
            s.expire(existing)
            refreshed = await s.get(ContractRow, contract_id)
            assert refreshed is not None
            return _to_contract_dto(refreshed)

    async def create_contract_change_request(
        self,
        *,
        contract_id: str,
        requested_by: str,
        proposed_body: dict[str, Any],
        reason: str,
        classified_as: str,
        status: str = "pending",
    ) -> ContractChangeRequestRecord:
        async with self._db.session_factory() as s, s.begin():
            contract = await s.get(ContractRow, contract_id)
            if contract is None:
                raise NotFoundError(f"Contract not found: {contract_id}")
            ccr_id = _new_id("ccr")
            row = ContractChangeRequestRow(
                id=ccr_id,
                contract_id=contract_id,
                requested_by=requested_by,
                proposed_body=dict(proposed_body),
                reason=reason,
                classified_as=classified_as,
                status=status,
            )
            s.add(row)
            s.add(
                EventRow(
                    run_id=contract.run_id,
                    type="contract.change_requested",
                    subject_id=ccr_id,
                    payload={
                        "change_request_id": ccr_id,
                        "contract_id": contract_id,
                        "classified_as": classified_as,
                        "status": status,
                    },
                )
            )
            await s.flush()
            await s.refresh(row)
            return _to_ccr_dto(row)

    async def list_contract_change_requests(
        self, contract_id: str
    ) -> list[ContractChangeRequestRecord]:
        async with self._db.session_factory() as s:
            rows = (
                await s.scalars(
                    select(ContractChangeRequestRow)
                    .where(ContractChangeRequestRow.contract_id == contract_id)
                    .order_by(ContractChangeRequestRow.created_at)
                )
            ).all()
            return [_to_ccr_dto(r) for r in rows]

    async def subscribe_task_to_contract(
        self,
        contract_id: str,
        task_id: str,
        scope: Sequence[str] = (),
    ) -> ContractSubscriptionRecord:
        scope_list = [str(x) for x in scope]
        async with self._db.session_factory() as s, s.begin():
            stmt = (
                pg_insert(ContractSubscriptionRow)
                .values(contract_id=contract_id, task_id=task_id, scope=scope_list)
                .on_conflict_do_update(
                    index_elements=["contract_id", "task_id"],
                    set_={"scope": scope_list},
                )
            )
            await s.execute(stmt)
            return ContractSubscriptionRecord(
                contract_id=contract_id,
                task_id=task_id,
                scope=scope_list,
            )

    async def list_contract_subscriptions(
        self, contract_id: str
    ) -> list[ContractSubscriptionRecord]:
        async with self._db.session_factory() as s:
            rows = (
                await s.scalars(
                    select(ContractSubscriptionRow).where(
                        ContractSubscriptionRow.contract_id == contract_id
                    )
                )
            ).all()
            return [
                ContractSubscriptionRecord(
                    contract_id=r.contract_id,
                    task_id=r.task_id,
                    scope=list(r.scope),
                )
                for r in rows
            ]

    async def update_contract_change_request(
        self,
        request_id: str,
        *,
        status: str,
        batch_id: str | None = None,
        classified_as: str | None = None,
    ) -> ContractChangeRequestRecord:
        async with self._db.session_factory() as s, s.begin():
            row = await s.get(ContractChangeRequestRow, request_id)
            if row is None:
                raise NotFoundError(f"ContractChangeRequest not found: {request_id}")
            row.status = status
            if batch_id is not None:
                row.batch_id = batch_id
            if classified_as is not None:
                row.classified_as = classified_as
            await s.flush()
            await s.refresh(row)
            return _to_ccr_dto(row)

    async def increment_contract_breaking_in_wave(
        self, contract_id: str, expected_version: int
    ) -> ContractRecord:
        async with self._db.session_factory() as s, s.begin():
            existing = await s.get(ContractRow, contract_id)
            if existing is None:
                raise NotFoundError(f"Contract not found: {contract_id}")
            res = await s.execute(
                update(ContractRow)
                .where(ContractRow.id == contract_id, ContractRow.version == expected_version)
                .values(
                    breaking_in_wave=ContractRow.breaking_in_wave + 1,
                    version=ContractRow.version + 1,
                )
            )
            if _rowcount(res) == 0:
                raise StaleWriteError(f"Stale write incrementing breaking_in_wave: {contract_id}")
            s.expire(existing)
            refreshed = await s.get(ContractRow, contract_id)
            assert refreshed is not None
            return _to_contract_dto(refreshed)

    async def upsert_feature(
        self,
        run_id: str,
        name: str,
        *,
        status: str = "pending",
        budget_blackbox_tests_usd: float = 0.30,
        spent_delta_usd: float = 0.0,
        dispute_rounds_delta: int = 0,
    ) -> FeatureRecord:
        now = datetime.now(UTC)
        async with self._db.session_factory() as s, s.begin():
            existing = await s.get(FeatureRow, (run_id, name))
            if existing is None:
                row = FeatureRow(
                    run_id=run_id,
                    name=name,
                    status=status,
                    budget_blackbox_tests_usd=budget_blackbox_tests_usd,
                    spent_blackbox_tests_usd=spent_delta_usd,
                    dispute_rounds=dispute_rounds_delta,
                    updated_at=now,
                )
                s.add(row)
                await s.flush()
                await s.refresh(row)
                return _to_feature_dto(row)
            existing.status = status
            existing.spent_blackbox_tests_usd = (
                float(existing.spent_blackbox_tests_usd) + spent_delta_usd
            )
            existing.dispute_rounds = int(existing.dispute_rounds) + dispute_rounds_delta
            existing.updated_at = now
            await s.flush()
            await s.refresh(existing)
            return _to_feature_dto(existing)

    async def list_features(self, run_id: str) -> list[FeatureRecord]:
        async with self._db.session_factory() as s:
            rows = (
                await s.scalars(
                    select(FeatureRow).where(FeatureRow.run_id == run_id).order_by(FeatureRow.name)
                )
            ).all()
            return [_to_feature_dto(r) for r in rows]

    async def get_feature(self, run_id: str, name: str) -> FeatureRecord | None:
        async with self._db.session_factory() as s:
            row = await s.get(FeatureRow, (run_id, name))
            return _to_feature_dto(row) if row is not None else None

    async def get_or_create_decision(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        category: str,
        title: str,
        context: str,
        options: Sequence[Any],
        mode: str,
        status: str = "pending",
        recommendation: Any | None = None,
        confidence: float | None = None,
        reversibility: str | None = "reversible",
        impact: dict[str, Any] | None = None,
        answer: Any | None = None,
        decided_by: str | None = None,
        graph_thread_id: str | None = None,
        blocks_task_ids: Sequence[str] = (),
    ) -> tuple[DecisionRecord, bool]:
        did = _new_id("dec")
        now = datetime.now(UTC) if status == "auto_decided" else None
        async with self._db.session_factory() as s, s.begin():
            stmt = (
                pg_insert(DecisionRow)
                .values(
                    id=did,
                    run_id=run_id,
                    idempotency_key=idempotency_key,
                    category=category,
                    title=title,
                    context=context,
                    options=list(options),
                    recommendation=recommendation,
                    confidence=confidence,
                    reversibility=reversibility,
                    impact=dict(impact or {}),
                    mode=mode,
                    status=status,
                    answer=answer,
                    decided_by=decided_by,
                    graph_thread_id=graph_thread_id,
                    blocks_task_ids=list(blocks_task_ids),
                    decided_at=now,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            res = await s.execute(stmt)
            created = _rowcount(res) > 0
            row = await s.scalar(
                select(DecisionRow).where(DecisionRow.idempotency_key == idempotency_key)
            )
            assert row is not None
            if created:
                s.add(
                    EventRow(
                        run_id=run_id,
                        type="decision.created",
                        subject_id=row.id,
                        payload={
                            "decision_id": row.id,
                            "category": category,
                            "mode": mode,
                            "status": status,
                        },
                    )
                )
                if status == "auto_decided":
                    s.add(
                        EventRow(
                            run_id=run_id,
                            type="decision.resolved",
                            subject_id=row.id,
                            payload={
                                "decision_id": row.id,
                                "status": status,
                                "decided_by": decided_by,
                            },
                        )
                    )
            return _to_decision_dto(row), created

    async def get_decision(self, decision_id: str) -> DecisionRecord:
        async with self._db.session_factory() as s:
            row = await s.get(DecisionRow, decision_id)
            if row is None:
                raise NotFoundError(f"Decision not found: {decision_id}")
            return _to_decision_dto(row)

    async def list_decisions(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
    ) -> list[DecisionRecord]:
        async with self._db.session_factory() as s:
            stmt = select(DecisionRow)
            if run_id is not None:
                stmt = stmt.where(DecisionRow.run_id == run_id)
            if status is not None:
                stmt = stmt.where(DecisionRow.status == status)
            stmt = stmt.order_by(DecisionRow.created_at, DecisionRow.id)
            rows = (await s.scalars(stmt)).all()
            return [_to_decision_dto(r) for r in rows]

    async def resolve_decision(
        self,
        decision_id: str,
        expected_version: int,
        *,
        status: str,
        answer: Any,
        decided_by: str,
    ) -> DecisionRecord:
        async with self._db.session_factory() as s, s.begin():
            existing = await s.get(DecisionRow, decision_id)
            if existing is None:
                raise NotFoundError(f"Decision not found: {decision_id}")
            res = await s.execute(
                update(DecisionRow)
                .where(
                    DecisionRow.id == decision_id,
                    DecisionRow.version == expected_version,
                )
                .values(
                    status=status,
                    answer=answer,
                    decided_by=decided_by,
                    decided_at=datetime.now(UTC),
                    version=DecisionRow.version + 1,
                )
            )
            if _rowcount(res) == 0:
                raise StaleWriteError(f"Stale write on decision {decision_id}")
            s.add(
                EventRow(
                    run_id=existing.run_id,
                    type="decision.resolved",
                    subject_id=decision_id,
                    payload={
                        "decision_id": decision_id,
                        "status": status,
                        "answer": answer,
                        "decided_by": decided_by,
                        "graph_thread_id": existing.graph_thread_id,
                    },
                )
            )
            s.expire(existing)
            refreshed = await s.get(DecisionRow, decision_id)
            assert refreshed is not None
            return _to_decision_dto(refreshed)

    async def post_note(
        self,
        *,
        run_id: str,
        kind: str,
        body: str,
        task_id: str | None = None,
        feature: str | None = None,
    ) -> NoteRecord:
        nid = _new_id("note")
        async with self._db.session_factory() as s, s.begin():
            row = NoteRow(
                id=nid,
                run_id=run_id,
                task_id=task_id,
                feature=feature,
                kind=kind,
                body=body,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return NoteRecord(
                id=row.id,
                run_id=row.run_id,
                task_id=row.task_id,
                feature=row.feature,
                kind=row.kind,
                body=row.body,
                created_at=row.created_at,
            )

    async def list_notes(self, run_id: str, *, feature: str | None = None) -> list[NoteRecord]:
        async with self._db.session_factory() as s:
            stmt = select(NoteRow).where(NoteRow.run_id == run_id)
            if feature is not None:
                stmt = stmt.where(NoteRow.feature == feature)
            stmt = stmt.order_by(NoteRow.created_at, NoteRow.id)
            rows = (await s.scalars(stmt)).all()
            return [
                NoteRecord(
                    id=r.id,
                    run_id=r.run_id,
                    task_id=r.task_id,
                    feature=r.feature,
                    kind=r.kind,
                    body=r.body,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def ensure_thread(
        self,
        *,
        run_id: str,
        kind: str,
        subject_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        tid = thread_id or _new_id("thr")
        async with self._db.session_factory() as s, s.begin():
            existing = await s.get(ThreadRow, tid)
            if existing is None:
                s.add(ThreadRow(id=tid, run_id=run_id, kind=kind, subject_id=subject_id))
            return tid

    async def post_message(
        self,
        *,
        thread_id: str,
        from_id: str,
        body: str,
        to_id: str | None = None,
    ) -> MessageRecord:
        mid = _new_id("msg")
        async with self._db.session_factory() as s, s.begin():
            row = MessageRow(
                id=mid,
                thread_id=thread_id,
                from_id=from_id,
                to_id=to_id,
                body=body,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return MessageRecord(
                id=row.id,
                thread_id=row.thread_id,
                from_id=row.from_id,
                to_id=row.to_id,
                body=row.body,
                created_at=row.created_at,
            )

    async def list_messages(self, thread_id: str) -> list[MessageRecord]:
        async with self._db.session_factory() as s:
            rows = (
                await s.scalars(
                    select(MessageRow)
                    .where(MessageRow.thread_id == thread_id)
                    .order_by(MessageRow.created_at, MessageRow.id)
                )
            ).all()
            return [
                MessageRecord(
                    id=r.id,
                    thread_id=r.thread_id,
                    from_id=r.from_id,
                    to_id=r.to_id,
                    body=r.body,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def record_llm_call(
        self,
        *,
        run_id: str | None,
        provider: str,
        model: str,
        profile: str,
        transport: str,
        billing_mode: str,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        cost_usd: float,
        est_value_usd: float,
        agent_id: str | None = None,
        task_id: str | None = None,
        graph_thread_id: str | None = None,
        node: str | None = None,
        latency_ms: int | None = None,
        replayed: bool = False,
        prompt_hash: str | None = None,
    ) -> LlmCallRecord:
        async with self._db.session_factory() as s, s.begin():
            row = LlmCallRow(
                run_id=run_id,
                agent_id=agent_id,
                task_id=task_id,
                graph_thread_id=graph_thread_id,
                node=node,
                provider=provider,
                model=model,
                profile=profile,
                transport=transport,
                billing_mode=billing_mode,
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                est_value_usd=est_value_usd,
                latency_ms=latency_ms,
                replayed=replayed,
                prompt_hash=prompt_hash,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return _to_llm_call_dto(row)

    async def list_llm_calls(self, run_id: str) -> list[LlmCallRecord]:
        async with self._db.session_factory() as s:
            rows = (
                await s.scalars(
                    select(LlmCallRow).where(LlmCallRow.run_id == run_id).order_by(LlmCallRow.id)
                )
            ).all()
            return [_to_llm_call_dto(r) for r in rows]

    async def emit_event(
        self,
        *,
        run_id: str,
        event_type: str,
        subject_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> BlackboardEvent:
        async with self._db.session_factory() as s, s.begin():
            row = EventRow(
                run_id=run_id,
                type=event_type,
                subject_id=subject_id,
                payload=dict(payload or {}),
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return _to_event_dto(row)

    async def publish_pending_events(self, limit: int = 500) -> list[BlackboardEvent]:
        async with self._db.session_factory() as s, s.begin():
            await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('events:publish'))"))
            res = await s.execute(
                text(
                    """
                    WITH ordered AS (
                        SELECT id, nextval('events_publish_seq') AS seq
                        FROM (
                            SELECT id
                            FROM events
                            WHERE publish_seq IS NULL
                            ORDER BY id
                            LIMIT :limit
                        ) s
                    )
                    UPDATE events e
                    SET publish_seq = o.seq,
                        published_at = now()
                    FROM ordered o
                    WHERE e.id = o.id
                    RETURNING e.id, e.publish_seq, e.run_id, e.type,
                              e.subject_id, e.payload, e.created_at,
                              e.published_at
                    """
                ),
                {"limit": limit},
            )
            rows = sorted(res.mappings().all(), key=lambda r: int(r["publish_seq"]))
            return [
                BlackboardEvent(
                    id=int(r["id"]),
                    publish_seq=int(r["publish_seq"]),
                    run_id=str(r["run_id"]),
                    type=str(r["type"]),
                    subject_id=r["subject_id"],
                    payload=dict(r["payload"]),
                    created_at=r["created_at"],
                    published_at=r["published_at"],
                )
                for r in rows
            ]

    async def mark_event_processed(self, consumer_group: str, publish_seq: int) -> bool:
        async with self._db.session_factory() as s, s.begin():
            res = await s.execute(
                pg_insert(ProcessedEventRow)
                .values(consumer_group=consumer_group, publish_seq=publish_seq)
                .on_conflict_do_nothing(index_elements=["consumer_group", "publish_seq"])
            )
            return _rowcount(res) > 0

    async def last_processed_seq(self, consumer_group: str) -> int:
        async with self._db.session_factory() as s:
            val = await s.scalar(
                select(text("COALESCE(MAX(publish_seq), 0)"))
                .select_from(ProcessedEventRow)
                .where(ProcessedEventRow.consumer_group == consumer_group)
            )
            return int(val or 0)

    async def list_events_after(
        self,
        after_seq: int = 0,
        *,
        run_id: str | None = None,
    ) -> list[BlackboardEvent]:
        async with self._db.session_factory() as s:
            stmt = select(EventRow).where(
                EventRow.publish_seq.is_not(None),
                EventRow.publish_seq > after_seq,
            )
            if run_id is not None:
                stmt = stmt.where(EventRow.run_id == run_id)
            stmt = stmt.order_by(EventRow.publish_seq)
            rows = (await s.scalars(stmt)).all()
            return [_to_event_dto(r) for r in rows]


class InMemoryBlackboardRepository:
    """In-memory BlackboardRepository preserving optimistic locking & monotonic publish_seq."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._runs: dict[str, RunRecord] = {}
        self._agents: dict[str, AgentRecord] = {}
        self._tasks: dict[str, TaskRecord] = {}
        self._deps: dict[str, list[str]] = {}
        self._write_sets: dict[str, list[str]] = {}
        self._reservations: list[WriteReservationRecord] = []
        self._next_reservation_id = 1
        self._contracts: dict[str, ContractRecord] = {}
        self._contract_versions: dict[tuple[str, int], ContractVersionRecord] = {}
        self._contract_subscriptions: dict[tuple[str, str], ContractSubscriptionRecord] = {}
        self._change_requests: dict[str, ContractChangeRequestRecord] = {}
        self._features: dict[tuple[str, str], FeatureRecord] = {}
        self._decisions: dict[str, DecisionRecord] = {}
        self._decision_keys: dict[str, str] = {}
        self._threads: dict[str, dict[str, Any]] = {}
        self._messages: dict[str, list[MessageRecord]] = {}
        self._notes: list[NoteRecord] = []
        self._llm_calls: list[LlmCallRecord] = []
        self._events: list[BlackboardEvent] = []
        self._processed: set[tuple[str, int]] = set()
        self._next_event_id = 1
        self._next_publish_seq = 1
        self._next_llm_id = 1
        self._on_change_callbacks: list[Any] = []

    def _save_hook(self) -> None:
        return None

    def _append_event(
        self,
        *,
        run_id: str,
        event_type: str,
        subject_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> BlackboardEvent:
        ev = BlackboardEvent(
            id=self._next_event_id,
            publish_seq=None,
            run_id=run_id,
            type=event_type,
            subject_id=subject_id,
            payload=dict(payload or {}),
            created_at=datetime.now(UTC),
            published_at=None,
        )
        self._next_event_id += 1
        self._events.append(ev)
        return ev

    async def create_run(
        self,
        *,
        project_id: str,
        budget_usd: float,
        data_policy: dict[str, Any] | None = None,
        run_id: str | None = None,
        phase: str = "planning",
    ) -> RunRecord:
        async with self._lock:
            rid = run_id or _new_id("run")
            rec = RunRecord(
                id=rid,
                project_id=project_id,
                phase=phase,
                budget_usd=budget_usd,
                spent_usd=0.0,
                data_policy=dict(
                    data_policy or {"allow_free_tier": False, "allow_trace_export": False}
                ),
                started_at=datetime.now(UTC),
            )
            self._runs[rid] = rec
            self._append_event(
                run_id=rid,
                event_type="run.created",
                subject_id=rid,
                payload={"run_id": rid, "project_id": project_id, "phase": phase},
            )
            self._save_hook()
            return rec

    async def get_run(self, run_id: str) -> RunRecord:
        if run_id not in self._runs:
            raise NotFoundError(f"Run not found: {run_id}")
        return self._runs[run_id]

    async def list_runs(self, project_id: str | None = None) -> list[RunRecord]:
        items = [r for r in self._runs.values() if project_id is None or r.project_id == project_id]
        items.sort(key=lambda r: (r.started_at, r.id), reverse=True)
        return items

    async def update_run_phase(
        self,
        run_id: str,
        phase: str,
        *,
        spent_usd: float | None = None,
        ended: bool = False,
    ) -> RunRecord:
        async with self._lock:
            cur = self._runs.get(run_id)
            if cur is None:
                raise NotFoundError(f"Run not found: {run_id}")
            updates: dict[str, Any] = {"phase": phase}
            if spent_usd is not None:
                updates["spent_usd"] = spent_usd
            if ended:
                updates["ended_at"] = datetime.now(UTC)
            updated = cur.model_copy(update=updates)
            self._runs[run_id] = updated
            self._append_event(
                run_id=run_id,
                event_type="run.phase_changed",
                subject_id=run_id,
                payload={"run_id": run_id, "phase": phase},
            )
            self._save_hook()
            return updated

    async def register_agent(
        self,
        *,
        run_id: str,
        role: str,
        model_profile: str,
        domain: str | None = None,
        parent_agent_id: str | None = None,
        graph_thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> AgentRecord:
        async with self._lock:
            aid = agent_id or _new_id("agt")
            rec = AgentRecord(
                id=aid,
                run_id=run_id,
                role=role,
                domain=domain,
                parent_agent_id=parent_agent_id,
                model_profile=model_profile,
                status="idle",
                graph_thread_id=graph_thread_id,
            )
            self._agents[aid] = rec
            self._save_hook()
            return rec

    async def update_agent_status(self, agent_id: str, status: str) -> AgentRecord:
        async with self._lock:
            cur = self._agents.get(agent_id)
            if cur is None:
                raise NotFoundError(f"Agent not found: {agent_id}")
            updated = cur.model_copy(update={"status": status})
            self._agents[agent_id] = updated
            self._save_hook()
            return updated

    async def list_agents(self, run_id: str) -> list[AgentRecord]:
        items = [a for a in self._agents.values() if a.run_id == run_id]
        items.sort(key=lambda a: a.id)
        return items

    async def get_agent(self, agent_id: str) -> AgentRecord:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent not found: {agent_id}")
        return agent

    async def create_task(
        self,
        *,
        run_id: str,
        wave: int,
        domain: str,
        title: str,
        description: str,
        budget_implement_usd: float,
        budget_review_usd: float,
        feature: str | None = None,
        kind: str = "build",
        status: TaskStatus = TaskStatus.PENDING,
        acceptance: Sequence[Any] = (),
        write_set: Sequence[str] = (),
        depends_on_task_ids: Sequence[str] = (),
        task_id: str | None = None,
        base_sha: str | None = None,
        graph_thread_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> TaskRecord:
        async with self._lock:
            tid = task_id or _new_id("t")
            now = datetime.now(UTC)
            rec = TaskRecord(
                id=tid,
                run_id=run_id,
                wave=wave,
                domain=domain,
                feature=feature,
                kind=kind,
                title=title,
                description=description,
                acceptance=list(acceptance),
                status=status,
                parent_task_id=parent_task_id,
                base_sha=base_sha,
                graph_thread_id=graph_thread_id,
                budget_implement_usd=budget_implement_usd,
                budget_review_usd=budget_review_usd,
                created_at=now,
                updated_at=now,
            )
            self._tasks[tid] = rec
            self._deps[tid] = list(depends_on_task_ids)
            self._write_sets[tid] = list(write_set)
            self._append_event(
                run_id=run_id,
                event_type=f"task.{status.value.lower()}",
                subject_id=tid,
                payload={"task_id": tid, "wave": wave, "domain": domain, "kind": kind},
            )
            self._save_hook()
            return rec

    async def get_task(self, task_id: str) -> TaskRecord:
        if task_id not in self._tasks:
            raise NotFoundError(f"Task not found: {task_id}")
        return self._tasks[task_id]

    async def list_tasks(
        self,
        run_id: str,
        *,
        wave: int | None = None,
        status: TaskStatus | None = None,
    ) -> list[TaskRecord]:
        items = [t for t in self._tasks.values() if t.run_id == run_id]
        if wave is not None:
            items = [t for t in items if t.wave == wave]
        if status is not None:
            items = [t for t in items if t.status == status]
        items.sort(key=lambda t: (t.wave, t.created_at, t.id))
        return items

    async def get_task_write_sets(self, task_id: str) -> list[str]:
        return list(self._write_sets.get(task_id, []))

    async def get_task_dependencies(self, task_id: str) -> list[str]:
        return list(self._deps.get(task_id, []))

    async def set_task_status(
        self,
        task_id: str,
        new_status: TaskStatus,
        expected_version: int,
        *,
        attempt: int | None = None,
        branch: str | None = None,
        base_sha: str | None = None,
        owner_agent_id: str | None = None,
        graph_thread_id: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> TaskRecord:
        async with self._lock:
            cur = self._tasks.get(task_id)
            if cur is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if cur.version != expected_version:
                raise StaleWriteError(
                    f"Stale write on task {task_id} (expected_version={expected_version})"
                )
            updates: dict[str, Any] = {
                "status": new_status,
                "version": cur.version + 1,
                "updated_at": datetime.now(UTC),
            }
            if attempt is not None:
                updates["attempt"] = attempt
            if branch is not None:
                updates["branch"] = branch
            if base_sha is not None:
                updates["base_sha"] = base_sha
            if owner_agent_id is not None:
                updates["owner_agent_id"] = owner_agent_id
            if graph_thread_id is not None:
                updates["graph_thread_id"] = graph_thread_id

            updated = cur.model_copy(update=updates)
            self._tasks[task_id] = updated
            self._append_event(
                run_id=cur.run_id,
                event_type=f"task.{new_status.value.lower()}",
                subject_id=task_id,
                payload={"task_id": task_id, "status": new_status.value, **(event_payload or {})},
            )
            self._save_hook()
            return updated

    async def increment_task_contract_reopens(
        self, task_id: str, expected_version: int
    ) -> TaskRecord:
        async with self._lock:
            cur = self._tasks.get(task_id)
            if cur is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if cur.version != expected_version:
                raise StaleWriteError(f"Stale write incrementing reopens on task {task_id}")
            updated = cur.model_copy(
                update={
                    "contract_reopens": cur.contract_reopens + 1,
                    "version": cur.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._tasks[task_id] = updated
            self._save_hook()
            return updated

    async def update_task_pending_deps(
        self,
        task_id: str,
        pending_deps: Sequence[dict[str, Any]],
        expected_version: int,
    ) -> TaskRecord:
        async with self._lock:
            cur = self._tasks.get(task_id)
            if cur is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if cur.version != expected_version:
                raise StaleWriteError(f"Stale write updating pending_deps on task {task_id}")
            updated = cur.model_copy(
                update={
                    "pending_deps": [dict(d) for d in pending_deps],
                    "version": cur.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._tasks[task_id] = updated
            self._save_hook()
            return updated

    async def update_task_metadata(
        self,
        task_id: str,
        expected_version: int,
        *,
        base_sha: str | None = None,
        branch: str | None = None,
        worktree_path: str | None = None,
        attempt: int | None = None,
        graph_thread_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> TaskRecord:
        async with self._lock:
            cur = self._tasks.get(task_id)
            if cur is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if cur.version != expected_version:
                raise StaleWriteError(f"Stale write updating metadata on task {task_id}")
            updates: dict[str, Any] = {
                "version": cur.version + 1,
                "updated_at": datetime.now(UTC),
            }
            if base_sha is not None:
                updates["base_sha"] = base_sha
            if branch is not None:
                updates["branch"] = branch
            if worktree_path is not None:
                updates["worktree_path"] = worktree_path
            if attempt is not None:
                updates["attempt"] = attempt
            if graph_thread_id is not None:
                updates["graph_thread_id"] = graph_thread_id
            if parent_task_id is not None:
                updates["parent_task_id"] = parent_task_id
            updated = cur.model_copy(update=updates)
            self._tasks[task_id] = updated
            self._save_hook()
            return updated

    async def increment_task_review_spend(self, task_id: str, delta_usd: float) -> TaskRecord:
        async with self._lock:
            cur = self._tasks.get(task_id)
            if cur is None:
                raise NotFoundError(f"Task not found: {task_id}")
            updated = cur.model_copy(
                update={
                    "spent_review_usd": cur.spent_review_usd + float(delta_usd),
                    "updated_at": datetime.now(UTC),
                }
            )
            self._tasks[task_id] = updated
            run = self._runs.get(cur.run_id)
            if run is not None:
                self._runs[cur.run_id] = run.model_copy(
                    update={"spent_usd": run.spent_usd + float(delta_usd)}
                )
            self._save_hook()
            return updated

    async def acquire_write_reservations(
        self,
        run_id: str,
        task_id: str,
        paths: Sequence[str],
        *,
        ttl_seconds: int = 600,
    ) -> tuple[list[WriteReservationRecord], list[ReservationConflict]]:
        norm_paths = list(
            dict.fromkeys(normalize_repo_path(p, allow_directory_slash=True) for p in paths)
        )
        if not norm_paths:
            return [], []
        async with self._lock:
            now = datetime.now(UTC)
            other_active = [
                r
                for r in self._reservations
                if r.run_id == run_id
                and r.task_id != task_id
                and r.released_at is None
                and r.expires_at > now
            ]
            conflicts: list[ReservationConflict] = []
            for other in other_active:
                for req_p, hold_p in paths_overlap(norm_paths, [other.path]):
                    conflicts.append(
                        ReservationConflict(
                            requested_path=req_p,
                            holder_task_id=other.task_id,
                            holder_path=hold_p,
                        )
                    )
            if conflicts:
                self._append_event(
                    run_id=run_id,
                    event_type="reservation.denied",
                    subject_id=task_id,
                    payload={
                        "task_id": task_id,
                        "conflicts": [c.model_dump(mode="json") for c in conflicts],
                    },
                )
                self._save_hook()
                return [], conflicts

            expires = now + timedelta(seconds=ttl_seconds)
            existing_own = {
                r.path: idx
                for idx, r in enumerate(self._reservations)
                if r.run_id == run_id and r.task_id == task_id and r.released_at is None
            }
            granted: list[WriteReservationRecord] = []
            for path in norm_paths:
                if path in existing_own:
                    idx = existing_own[path]
                    updated = self._reservations[idx].model_copy(
                        update={"expires_at": expires, "heartbeat_at": now}
                    )
                    self._reservations[idx] = updated
                    granted.append(updated)
                else:
                    rec = WriteReservationRecord(
                        id=self._next_reservation_id,
                        run_id=run_id,
                        task_id=task_id,
                        path=path,
                        expires_at=expires,
                        heartbeat_at=now,
                    )
                    self._next_reservation_id += 1
                    self._reservations.append(rec)
                    granted.append(rec)
            self._append_event(
                run_id=run_id,
                event_type="reservation.granted",
                subject_id=task_id,
                payload={"task_id": task_id, "paths": norm_paths},
            )
            self._save_hook()
            return granted, []

    async def renew_write_reservations(
        self,
        run_id: str,
        *,
        task_ids: Sequence[str] | None = None,
        ttl_seconds: int = 600,
    ) -> int:
        async with self._lock:
            now = datetime.now(UTC)
            expires = now + timedelta(seconds=ttl_seconds)
            id_filter = set(task_ids) if task_ids is not None else None
            if id_filter is not None and not id_filter:
                return 0
            renewed = 0
            for idx, r in enumerate(self._reservations):
                if (
                    r.run_id == run_id
                    and r.released_at is None
                    and r.expires_at > now
                    and (id_filter is None or r.task_id in id_filter)
                ):
                    self._reservations[idx] = r.model_copy(
                        update={"expires_at": expires, "heartbeat_at": now}
                    )
                    renewed += 1
            if renewed:
                self._save_hook()
            return renewed

    async def release_write_reservations(
        self,
        run_id: str,
        task_id: str,
        *,
        reason: str = "released",
    ) -> int:
        async with self._lock:
            now = datetime.now(UTC)
            count = 0
            for idx, r in enumerate(self._reservations):
                if r.run_id == run_id and r.task_id == task_id and r.released_at is None:
                    self._reservations[idx] = r.model_copy(update={"released_at": now})
                    count += 1
            if count > 0:
                ev_type = "reservation.expired" if reason == "expired" else "reservation.released"
                self._append_event(
                    run_id=run_id,
                    event_type=ev_type,
                    subject_id=task_id,
                    payload={"task_id": task_id, "count": count, "reason": reason},
                )
                self._save_hook()
            return count

    async def list_active_reservations(
        self,
        run_id: str,
        *,
        include_expired: bool = False,
    ) -> list[WriteReservationRecord]:
        now = datetime.now(UTC)
        return [
            r
            for r in self._reservations
            if r.run_id == run_id
            and r.released_at is None
            and (include_expired or r.expires_at > now)
        ]

    async def save_contract_version(
        self,
        *,
        run_id: str,
        name: str,
        domain: str,
        kind: str,
        body: dict[str, Any],
        author_agent_id: str | None = None,
        pinned_pointers: Sequence[str] = (),
        change_kind: str = "initial",
        changed_elements: Sequence[str] = (),
        change_summary: str | None = None,
        approved_by: str | None = None,
    ) -> tuple[ContractRecord, ContractVersionRecord]:
        async with self._lock:
            existing = next(
                (c for c in self._contracts.values() if c.run_id == run_id and c.name == name),
                None,
            )
            if existing is None:
                cid = _new_id("ctr")
                ctr = ContractRecord(
                    id=cid,
                    run_id=run_id,
                    name=name,
                    domain=domain,
                    kind=kind,
                    owner_agent_id=author_agent_id,
                    current_version=1,
                    status="draft",
                    breaking_in_wave=0,
                    version=1,
                )
                new_ver = 1
            else:
                new_ver = existing.current_version + 1
                ctr = existing.model_copy(
                    update={
                        "current_version": new_ver,
                        "version": existing.version + 1,
                        "breaking_in_wave": (
                            existing.breaking_in_wave + (1 if change_kind == "breaking" else 0)
                        ),
                    }
                )
            self._contracts[ctr.id] = ctr
            ver = ContractVersionRecord(
                contract_id=ctr.id,
                version=new_ver,
                body=dict(body),
                pinned_pointers=list(pinned_pointers),
                change_kind=change_kind,
                changed_elements=list(changed_elements),
                change_summary=change_summary,
                author_agent_id=author_agent_id,
                approved_by=approved_by,
                created_at=datetime.now(UTC),
            )
            self._contract_versions[(ctr.id, new_ver)] = ver
            self._append_event(
                run_id=run_id,
                event_type="contract.versioned",
                subject_id=ctr.id,
                payload={"contract_id": ctr.id, "name": name, "version": new_ver},
            )
            self._save_hook()
            return ctr, ver

    async def get_contract(self, run_id: str, name: str) -> ContractRecord:
        for ctr in self._contracts.values():
            if ctr.run_id == run_id and ctr.name == name:
                return ctr
        raise NotFoundError(f"Contract not found: {run_id}/{name}")

    async def get_contract_by_id(self, contract_id: str) -> ContractRecord:
        if contract_id not in self._contracts:
            raise NotFoundError(f"Contract not found: {contract_id}")
        return self._contracts[contract_id]

    async def list_contracts(self, run_id: str) -> list[ContractRecord]:
        items = [c for c in self._contracts.values() if c.run_id == run_id]
        items.sort(key=lambda c: c.name)
        return items

    async def get_latest_contract_version(self, contract_id: str) -> ContractVersionRecord:
        ctr = await self.get_contract_by_id(contract_id)
        key = (contract_id, ctr.current_version)
        if key not in self._contract_versions:
            raise NotFoundError(f"No versions for contract {contract_id}")
        return self._contract_versions[key]

    async def list_contract_versions(self, contract_id: str) -> list[ContractVersionRecord]:
        await self.get_contract_by_id(contract_id)
        items = [v for (cid, _), v in self._contract_versions.items() if cid == contract_id]
        items.sort(key=lambda v: v.version)
        return items

    async def lock_contract(
        self,
        contract_id: str,
        expected_version: int,
        *,
        status: str = "locked",
    ) -> ContractRecord:
        async with self._lock:
            cur = self._contracts.get(contract_id)
            if cur is None:
                raise NotFoundError(f"Contract not found: {contract_id}")
            if cur.version != expected_version:
                raise StaleWriteError(f"Stale write on contract {contract_id}")
            updated = cur.model_copy(update={"status": status, "version": cur.version + 1})
            self._contracts[contract_id] = updated
            self._append_event(
                run_id=cur.run_id,
                event_type=f"contract.{status}",
                subject_id=contract_id,
                payload={"contract_id": contract_id, "name": cur.name, "status": status},
            )
            self._save_hook()
            return updated

    async def create_contract_change_request(
        self,
        *,
        contract_id: str,
        requested_by: str,
        proposed_body: dict[str, Any],
        reason: str,
        classified_as: str,
        status: str = "pending",
    ) -> ContractChangeRequestRecord:
        async with self._lock:
            ctr = self._contracts.get(contract_id)
            if ctr is None:
                raise NotFoundError(f"Contract not found: {contract_id}")
            ccr = ContractChangeRequestRecord(
                id=_new_id("ccr"),
                contract_id=contract_id,
                requested_by=requested_by,
                proposed_body=dict(proposed_body),
                reason=reason,
                classified_as=classified_as,
                status=status,
                created_at=datetime.now(UTC),
            )
            self._change_requests[ccr.id] = ccr
            self._append_event(
                run_id=ctr.run_id,
                event_type="contract.change_requested",
                subject_id=ccr.id,
                payload={
                    "change_request_id": ccr.id,
                    "contract_id": contract_id,
                    "classified_as": classified_as,
                    "status": status,
                },
            )
            self._save_hook()
            return ccr

    async def list_contract_change_requests(
        self, contract_id: str
    ) -> list[ContractChangeRequestRecord]:
        return [c for c in self._change_requests.values() if c.contract_id == contract_id]

    async def subscribe_task_to_contract(
        self,
        contract_id: str,
        task_id: str,
        scope: Sequence[str] = (),
    ) -> ContractSubscriptionRecord:
        async with self._lock:
            rec = ContractSubscriptionRecord(
                contract_id=contract_id,
                task_id=task_id,
                scope=[str(x) for x in scope],
            )
            self._contract_subscriptions[(contract_id, task_id)] = rec
            self._save_hook()
            return rec

    async def list_contract_subscriptions(
        self, contract_id: str
    ) -> list[ContractSubscriptionRecord]:
        return [sub for (cid, _), sub in self._contract_subscriptions.items() if cid == contract_id]

    async def update_contract_change_request(
        self,
        request_id: str,
        *,
        status: str,
        batch_id: str | None = None,
        classified_as: str | None = None,
    ) -> ContractChangeRequestRecord:
        async with self._lock:
            cur = self._change_requests.get(request_id)
            if cur is None:
                raise NotFoundError(f"ContractChangeRequest not found: {request_id}")
            updates: dict[str, Any] = {"status": status}
            if batch_id is not None:
                updates["batch_id"] = batch_id
            if classified_as is not None:
                updates["classified_as"] = classified_as
            updated = cur.model_copy(update=updates)
            self._change_requests[request_id] = updated
            self._save_hook()
            return updated

    async def increment_contract_breaking_in_wave(
        self, contract_id: str, expected_version: int
    ) -> ContractRecord:
        async with self._lock:
            cur = self._contracts.get(contract_id)
            if cur is None:
                raise NotFoundError(f"Contract not found: {contract_id}")
            if cur.version != expected_version:
                raise StaleWriteError(f"Stale write incrementing breaking_in_wave: {contract_id}")
            updated = cur.model_copy(
                update={
                    "breaking_in_wave": cur.breaking_in_wave + 1,
                    "version": cur.version + 1,
                }
            )
            self._contracts[contract_id] = updated
            self._save_hook()
            return updated

    async def upsert_feature(
        self,
        run_id: str,
        name: str,
        *,
        status: str = "pending",
        budget_blackbox_tests_usd: float = 0.30,
        spent_delta_usd: float = 0.0,
        dispute_rounds_delta: int = 0,
    ) -> FeatureRecord:
        async with self._lock:
            now = datetime.now(UTC)
            key = (run_id, name)
            cur = self._features.get(key)
            if cur is None:
                rec = FeatureRecord(
                    run_id=run_id,
                    name=name,
                    status=status,
                    budget_blackbox_tests_usd=budget_blackbox_tests_usd,
                    spent_blackbox_tests_usd=spent_delta_usd,
                    dispute_rounds=dispute_rounds_delta,
                    updated_at=now,
                )
            else:
                rec = cur.model_copy(
                    update={
                        "status": status,
                        "spent_blackbox_tests_usd": cur.spent_blackbox_tests_usd + spent_delta_usd,
                        "dispute_rounds": cur.dispute_rounds + dispute_rounds_delta,
                        "updated_at": now,
                    }
                )
            self._features[key] = rec
            self._save_hook()
            return rec

    async def list_features(self, run_id: str) -> list[FeatureRecord]:
        items = [f for (rid, _), f in self._features.items() if rid == run_id]
        items.sort(key=lambda f: f.name)
        return items

    async def get_feature(self, run_id: str, name: str) -> FeatureRecord | None:
        return self._features.get((run_id, name))

    async def get_or_create_decision(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        category: str,
        title: str,
        context: str,
        options: Sequence[Any],
        mode: str,
        status: str = "pending",
        recommendation: Any | None = None,
        confidence: float | None = None,
        reversibility: str | None = "reversible",
        impact: dict[str, Any] | None = None,
        answer: Any | None = None,
        decided_by: str | None = None,
        graph_thread_id: str | None = None,
        blocks_task_ids: Sequence[str] = (),
    ) -> tuple[DecisionRecord, bool]:
        async with self._lock:
            if idempotency_key in self._decision_keys:
                did = self._decision_keys[idempotency_key]
                return self._decisions[did], False
            did = _new_id("dec")
            now = datetime.now(UTC)
            rec = DecisionRecord(
                id=did,
                run_id=run_id,
                idempotency_key=idempotency_key,
                category=category,
                title=title,
                context=context,
                options=list(options),
                recommendation=recommendation,
                confidence=confidence,
                reversibility=reversibility,
                impact=dict(impact or {}),
                mode=mode,
                status=status,
                answer=answer,
                decided_by=decided_by,
                graph_thread_id=graph_thread_id,
                blocks_task_ids=list(blocks_task_ids),
                version=1,
                created_at=now,
                decided_at=now if status == "auto_decided" else None,
            )
            self._decisions[did] = rec
            self._decision_keys[idempotency_key] = did
            self._append_event(
                run_id=run_id,
                event_type="decision.created",
                subject_id=did,
                payload={"decision_id": did, "category": category, "mode": mode, "status": status},
            )
            if status == "auto_decided":
                self._append_event(
                    run_id=run_id,
                    event_type="decision.resolved",
                    subject_id=did,
                    payload={"decision_id": did, "status": status, "decided_by": decided_by},
                )
            self._save_hook()
            return rec, True

    async def get_decision(self, decision_id: str) -> DecisionRecord:
        if decision_id not in self._decisions:
            raise NotFoundError(f"Decision not found: {decision_id}")
        return self._decisions[decision_id]

    async def list_decisions(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
    ) -> list[DecisionRecord]:
        items = list(self._decisions.values())
        if run_id is not None:
            items = [d for d in items if d.run_id == run_id]
        if status is not None:
            items = [d for d in items if d.status == status]
        items.sort(key=lambda d: (d.created_at, d.id))
        return items

    async def resolve_decision(
        self,
        decision_id: str,
        expected_version: int,
        *,
        status: str,
        answer: Any,
        decided_by: str,
    ) -> DecisionRecord:
        async with self._lock:
            cur = self._decisions.get(decision_id)
            if cur is None:
                raise NotFoundError(f"Decision not found: {decision_id}")
            if cur.version != expected_version:
                raise StaleWriteError(f"Stale write on decision {decision_id}")
            updated = cur.model_copy(
                update={
                    "status": status,
                    "answer": answer,
                    "decided_by": decided_by,
                    "decided_at": datetime.now(UTC),
                    "version": cur.version + 1,
                }
            )
            self._decisions[decision_id] = updated
            self._append_event(
                run_id=cur.run_id,
                event_type="decision.resolved",
                subject_id=decision_id,
                payload={
                    "decision_id": decision_id,
                    "status": status,
                    "answer": answer,
                    "decided_by": decided_by,
                    "graph_thread_id": cur.graph_thread_id,
                },
            )
            self._save_hook()
            return updated

    async def post_note(
        self,
        *,
        run_id: str,
        kind: str,
        body: str,
        task_id: str | None = None,
        feature: str | None = None,
    ) -> NoteRecord:
        async with self._lock:
            rec = NoteRecord(
                id=_new_id("note"),
                run_id=run_id,
                task_id=task_id,
                feature=feature,
                kind=kind,
                body=body,
                created_at=datetime.now(UTC),
            )
            self._notes.append(rec)
            self._save_hook()
            return rec

    async def list_notes(self, run_id: str, *, feature: str | None = None) -> list[NoteRecord]:
        items = [n for n in self._notes if n.run_id == run_id]
        if feature is not None:
            items = [n for n in items if n.feature == feature]
        return items

    async def ensure_thread(
        self,
        *,
        run_id: str,
        kind: str,
        subject_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        async with self._lock:
            tid = thread_id or _new_id("thr")
            if tid not in self._threads:
                self._threads[tid] = {"run_id": run_id, "kind": kind, "subject_id": subject_id}
                self._messages.setdefault(tid, [])
                self._save_hook()
            return tid

    async def post_message(
        self,
        *,
        thread_id: str,
        from_id: str,
        body: str,
        to_id: str | None = None,
    ) -> MessageRecord:
        async with self._lock:
            rec = MessageRecord(
                id=_new_id("msg"),
                thread_id=thread_id,
                from_id=from_id,
                to_id=to_id,
                body=body,
                created_at=datetime.now(UTC),
            )
            self._messages.setdefault(thread_id, []).append(rec)
            self._save_hook()
            return rec

    async def list_messages(self, thread_id: str) -> list[MessageRecord]:
        return list(self._messages.get(thread_id, []))

    async def record_llm_call(
        self,
        *,
        run_id: str | None,
        provider: str,
        model: str,
        profile: str,
        transport: str,
        billing_mode: str,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        cost_usd: float,
        est_value_usd: float,
        agent_id: str | None = None,
        task_id: str | None = None,
        graph_thread_id: str | None = None,
        node: str | None = None,
        latency_ms: int | None = None,
        replayed: bool = False,
        prompt_hash: str | None = None,
    ) -> LlmCallRecord:
        async with self._lock:
            rec = LlmCallRecord(
                id=self._next_llm_id,
                run_id=run_id,
                agent_id=agent_id,
                task_id=task_id,
                graph_thread_id=graph_thread_id,
                node=node,
                provider=provider,
                model=model,
                profile=profile,
                transport=transport,
                billing_mode=billing_mode,
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                est_value_usd=est_value_usd,
                latency_ms=latency_ms,
                replayed=replayed,
                prompt_hash=prompt_hash,
                created_at=datetime.now(UTC),
            )
            self._next_llm_id += 1
            self._llm_calls.append(rec)
            self._save_hook()
            return rec

    async def list_llm_calls(self, run_id: str) -> list[LlmCallRecord]:
        items = [c for c in self._llm_calls if c.run_id == run_id]
        items.sort(key=lambda c: c.id)
        return items

    async def emit_event(
        self,
        *,
        run_id: str,
        event_type: str,
        subject_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> BlackboardEvent:
        async with self._lock:
            ev = self._append_event(
                run_id=run_id,
                event_type=event_type,
                subject_id=subject_id,
                payload=payload,
            )
            self._save_hook()
            return ev

    async def publish_pending_events(self, limit: int = 500) -> list[BlackboardEvent]:
        async with self._lock:
            published: list[BlackboardEvent] = []
            now = datetime.now(UTC)
            for idx, ev in enumerate(self._events):
                if ev.publish_seq is None:
                    updated = ev.model_copy(
                        update={"publish_seq": self._next_publish_seq, "published_at": now}
                    )
                    self._events[idx] = updated
                    self._next_publish_seq += 1
                    published.append(updated)
                    if len(published) >= limit:
                        break
            if published:
                self._save_hook()
            return published

    async def mark_event_processed(self, consumer_group: str, publish_seq: int) -> bool:
        async with self._lock:
            key = (consumer_group, publish_seq)
            if key in self._processed:
                return False
            self._processed.add(key)
            self._save_hook()
            return True

    async def last_processed_seq(self, consumer_group: str) -> int:
        seqs = [seq for grp, seq in self._processed if grp == consumer_group]
        return max(seqs) if seqs else 0

    async def list_events_after(
        self,
        after_seq: int = 0,
        *,
        run_id: str | None = None,
    ) -> list[BlackboardEvent]:
        items = [
            ev
            for ev in self._events
            if ev.publish_seq is not None
            and ev.publish_seq > after_seq
            and (run_id is None or ev.run_id == run_id)
        ]
        items.sort(key=lambda e: int(e.publish_seq or 0))
        return items


class JsonFileBlackboardRepository(InMemoryBlackboardRepository):
    """Disk-persisted Blackboard repository for stateless CLI tests without Postgres."""

    def __init__(self, state_file: Path) -> None:
        super().__init__()
        self._state_file = state_file
        if self._state_file.is_file():
            self._load()

    def _load(self) -> None:
        raw = json.loads(self._state_file.read_text(encoding="utf-8"))
        self._runs = {k: RunRecord.model_validate(v) for k, v in raw.get("runs", {}).items()}
        self._agents = {k: AgentRecord.model_validate(v) for k, v in raw.get("agents", {}).items()}
        self._tasks = {k: TaskRecord.model_validate(v) for k, v in raw.get("tasks", {}).items()}
        self._deps = dict(raw.get("deps", {}))
        self._write_sets = dict(raw.get("write_sets", {}))
        self._reservations = [
            WriteReservationRecord.model_validate(r) for r in raw.get("reservations", [])
        ]
        self._next_reservation_id = int(raw.get("next_reservation_id", 1))
        self._contracts = {
            k: ContractRecord.model_validate(v) for k, v in raw.get("contracts", {}).items()
        }
        self._contract_versions = {
            (k.split("::", 1)[0], int(k.split("::", 1)[1])): ContractVersionRecord.model_validate(v)
            for k, v in raw.get("contract_versions", {}).items()
        }
        self._contract_subscriptions = {
            (k.split("::", 1)[0], k.split("::", 1)[1]): ContractSubscriptionRecord.model_validate(v)
            for k, v in raw.get("contract_subscriptions", {}).items()
        }
        self._change_requests = {
            k: ContractChangeRequestRecord.model_validate(v)
            for k, v in raw.get("change_requests", {}).items()
        }
        self._features = {
            (k.split("::", 1)[0], k.split("::", 1)[1]): FeatureRecord.model_validate(v)
            for k, v in raw.get("features", {}).items()
        }
        self._decisions = {
            k: DecisionRecord.model_validate(v) for k, v in raw.get("decisions", {}).items()
        }
        self._decision_keys = dict(raw.get("decision_keys", {}))
        self._threads = dict(raw.get("threads", {}))
        self._messages = {
            k: [MessageRecord.model_validate(m) for m in v]
            for k, v in raw.get("messages", {}).items()
        }
        self._notes = [NoteRecord.model_validate(n) for n in raw.get("notes", [])]
        self._llm_calls = [LlmCallRecord.model_validate(c) for c in raw.get("llm_calls", [])]
        self._events = [BlackboardEvent.model_validate(e) for e in raw.get("events", [])]
        self._processed = {(pair[0], int(pair[1])) for pair in raw.get("processed", [])}
        self._next_event_id = int(raw.get("next_event_id", 1))
        self._next_publish_seq = int(raw.get("next_publish_seq", 1))
        self._next_llm_id = int(raw.get("next_llm_id", 1))

    def _save_hook(self) -> None:
        payload = {
            "runs": {k: v.model_dump(mode="json") for k, v in self._runs.items()},
            "agents": {k: v.model_dump(mode="json") for k, v in self._agents.items()},
            "tasks": {k: v.model_dump(mode="json") for k, v in self._tasks.items()},
            "deps": self._deps,
            "write_sets": self._write_sets,
            "reservations": [r.model_dump(mode="json") for r in self._reservations],
            "next_reservation_id": self._next_reservation_id,
            "contracts": {k: v.model_dump(mode="json") for k, v in self._contracts.items()},
            "contract_versions": {
                f"{cid}::{ver}": v.model_dump(mode="json")
                for (cid, ver), v in self._contract_versions.items()
            },
            "contract_subscriptions": {
                f"{cid}::{tid}": s.model_dump(mode="json")
                for (cid, tid), s in self._contract_subscriptions.items()
            },
            "change_requests": {
                k: v.model_dump(mode="json") for k, v in self._change_requests.items()
            },
            "features": {
                f"{rid}::{fname}": f.model_dump(mode="json")
                for (rid, fname), f in self._features.items()
            },
            "decisions": {k: v.model_dump(mode="json") for k, v in self._decisions.items()},
            "decision_keys": self._decision_keys,
            "threads": self._threads,
            "messages": {
                k: [m.model_dump(mode="json") for m in v] for k, v in self._messages.items()
            },
            "notes": [n.model_dump(mode="json") for n in self._notes],
            "llm_calls": [c.model_dump(mode="json") for c in self._llm_calls],
            "events": [e.model_dump(mode="json") for e in self._events],
            "processed": [[g, s] for g, s in sorted(self._processed)],
            "next_event_id": self._next_event_id,
            "next_publish_seq": self._next_publish_seq,
            "next_llm_id": self._next_llm_id,
        }
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
