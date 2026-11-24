"""Parallel wave scheduler coordinating max_concurrent_tasks, reservations & merge queue."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from foundry.blackboard.events import EventRelay
from foundry.blackboard.models import TaskRecord, TaskStatus
from foundry.blackboard.repository import BlackboardRepository
from foundry.concurrency.merge_queue import MergeOutcome, SerialMergeQueue
from foundry.concurrency.reservations import WriteReservationManager
from foundry.contracts.protocol import ContractChangeProtocol
from foundry.decisions.service import DecisionService
from foundry.supervisor.waves import advance_ready_tasks

ImplementerResultLike = Any
ParallelWorkerFn = Callable[[TaskRecord, dict[str, Any]], Awaitable[ImplementerResultLike]]


async def _noop_parallel_worker(task: TaskRecord, context: dict[str, Any]) -> dict[str, Any]:
    _ = (task, context)
    return {"status": "verified_g2"}


class ParallelWaveScheduler:
    """Runs independent task threads concurrently under write_reservations + SerialMergeQueue."""

    def __init__(
        self,
        repo: BlackboardRepository,
        reservations: WriteReservationManager,
        merge_queue: SerialMergeQueue,
        relay: EventRelay,
        *,
        contract_protocol: ContractChangeProtocol | None = None,
        decisions: DecisionService | None = None,
        worker_fn: ParallelWorkerFn | None = None,
        max_concurrent_tasks: int = 4,
        heartbeat_interval_s: float = 60.0,
    ) -> None:
        self._repo = repo
        self._reservations = reservations
        self._merge_queue = merge_queue
        self._relay = relay
        self._contract_protocol = contract_protocol
        self._decisions = decisions
        self._worker_fn = worker_fn or _noop_parallel_worker
        self.max_concurrent_tasks = max(1, max_concurrent_tasks)
        self.heartbeat_interval_s = heartbeat_interval_s
        self._schema_boundary_refs: dict[str, dict[str, str | None]] = {}

    @staticmethod
    def _extract_status_and_sha(result: Any) -> tuple[str, str | None, str | None]:
        if isinstance(result, dict):
            return (
                str(result.get("status", "verified_g2")),
                result.get("new_base_sha"),
                result.get("error"),
            )
        if result is None:
            return "verified_g2", None, None
        return (
            str(getattr(result, "status", "verified_g2")),
            getattr(result, "new_base_sha", None),
            getattr(result, "error", None),
        )

    async def _handle_finished_worker(
        self,
        *,
        run_id: str,
        task_id: str,
        result: Any,
        merge_outcomes: list[MergeOutcome],
    ) -> None:
        self._schema_boundary_refs.pop(task_id, None)
        status, new_base_sha, err = self._extract_status_and_sha(result)
        latest = await self._repo.get_task(task_id)

        if status == "verified_g2":
            if latest.status == TaskStatus.IN_PROGRESS:
                latest = await self._repo.set_task_status(
                    latest.id, TaskStatus.IN_REVIEW, latest.version
                )
            if latest.status == TaskStatus.IN_REVIEW:
                latest = await self._repo.set_task_status(
                    latest.id, TaskStatus.VERIFIED, latest.version
                )
            outcome = await self._merge_queue.process_task(run_id, latest.id)
            merge_outcomes.append(outcome)
            if outcome.status == "merged" and latest.kind == "schema" and outcome.merged_sha:
                for ref in self._schema_boundary_refs.values():
                    ref["main_sha"] = outcome.merged_sha
            return

        await self._reservations.release(run_id, latest.id, reason="released")
        if status == "schema_rebase_restart":
            if latest.attempt + 1 < latest.max_attempts:
                await self._repo.set_task_status(
                    latest.id,
                    TaskStatus.READY,
                    latest.version,
                    attempt=latest.attempt + 1,
                    base_sha=new_base_sha,
                    event_payload={"requeued_after_schema_conflict": True, "error": err},
                )
            else:
                await self._repo.set_task_status(
                    latest.id,
                    TaskStatus.ESCALATED,
                    latest.version,
                    event_payload={"escalated_after_schema_conflict": True, "error": err},
                )
            return

        # Failed / budget_exceeded
        if latest.attempt + 1 < latest.max_attempts:
            await self._repo.set_task_status(
                latest.id,
                TaskStatus.READY,
                latest.version,
                attempt=latest.attempt + 1,
                event_payload={"retry_after_failure": True, "error": err},
            )
        else:
            await self._repo.set_task_status(
                latest.id,
                TaskStatus.ESCALATED,
                latest.version,
                event_payload={"failed_all_attempts": True, "error": err},
            )

    async def run_wave(
        self,
        run_id: str,
        *,
        wave: int,
        contracts: dict[str, dict[str, Any]] | None = None,
    ) -> list[MergeOutcome]:
        """Execute wave tasks concurrently with reservation conflict gating + serial merges."""
        contract_map = dict(contracts or {})
        if not contract_map:
            for ctr in await self._repo.list_contracts(run_id):
                ver = await self._repo.get_latest_contract_version(ctr.id)
                contract_map[ctr.name] = ver.body

        active_workers: dict[str, asyncio.Task[Any]] = {}
        merge_outcomes: list[MergeOutcome] = []
        heartbeat = self._reservations.start_heartbeat(run_id, interval_s=self.heartbeat_interval_s)

        try:
            while True:
                await advance_ready_tasks(self._repo, run_id)

                if self._contract_protocol is not None:
                    for ctr in await self._repo.list_contracts(run_id):
                        await self._contract_protocol.flush_breaking_batch(ctr.id, force=False)

                wave_tasks = await self._repo.list_tasks(run_id, wave=wave)

                # Merge any tasks already sitting in VERIFIED
                for vt in wave_tasks:
                    if vt.status == TaskStatus.VERIFIED and vt.id not in active_workers:
                        outcome = await self._merge_queue.process_task(run_id, vt.id)
                        merge_outcomes.append(outcome)
                        if (
                            outcome.status == "merged"
                            and vt.kind == "schema"
                            and outcome.merged_sha
                        ):
                            for ref in self._schema_boundary_refs.values():
                                ref["main_sha"] = outcome.merged_sha
                        wave_tasks = await self._repo.list_tasks(run_id, wave=wave)

                if (
                    wave_tasks
                    and not active_workers
                    and all(
                        t.status in {TaskStatus.MERGED, TaskStatus.CANCELLED} for t in wave_tasks
                    )
                ):
                    await self._repo.emit_event(
                        run_id=run_id,
                        event_type="wave.completed",
                        subject_id=run_id,
                        payload={"run_id": run_id, "wave": wave},
                    )
                    await self._relay.flush_once()
                    return merge_outcomes

                # Schedule READY tasks while below max_concurrent_tasks and reservation succeeds
                ready_tasks = [
                    t
                    for t in wave_tasks
                    if t.status == TaskStatus.READY and t.id not in active_workers
                ]
                scheduled_any = False
                for task in ready_tasks:
                    if len(active_workers) >= self.max_concurrent_tasks:
                        break
                    write_set = await self._repo.get_task_write_sets(task.id)
                    granted, conflicts = await self._reservations.acquire(
                        run_id, task.id, write_set
                    )
                    if conflicts or (write_set and not granted):
                        continue

                    branch_name = f"task/{task.id}-a{task.attempt + 1}"
                    thread_id = f"{run_id}:task:{task.id}:{task.attempt + 1}"
                    t_prog = await self._repo.set_task_status(
                        task.id,
                        TaskStatus.IN_PROGRESS,
                        task.version,
                        branch=branch_name,
                        graph_thread_id=thread_id,
                    )
                    boundary_ref: dict[str, str | None] = {"main_sha": None}
                    self._schema_boundary_refs[t_prog.id] = boundary_ref
                    ctx = {
                        "contracts": contract_map,
                        "wave": wave,
                        "write_set": write_set,
                        "boundary_schema_ref": boundary_ref,
                    }

                    async def _invoke_worker(
                        w_task: TaskRecord = t_prog, w_ctx: dict[str, Any] = ctx
                    ) -> Any:
                        return await self._worker_fn(w_task, w_ctx)

                    active_workers[t_prog.id] = asyncio.create_task(
                        _invoke_worker(),
                        name=f"foundry-worker:{t_prog.id}",
                    )
                    scheduled_any = True

                await self._relay.flush_once()

                if active_workers:
                    done_set, _ = await asyncio.wait(
                        active_workers.values(),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    finished_ids = [tid for tid, aw in active_workers.items() if aw in done_set]
                    for tid in finished_ids:
                        worker_task = active_workers.pop(tid)
                        try:
                            res = worker_task.result()
                        except Exception as exc:
                            res = {"status": "failed", "error": str(exc)}
                        await self._handle_finished_worker(
                            run_id=run_id,
                            task_id=tid,
                            result=res,
                            merge_outcomes=merge_outcomes,
                        )
                    await self._relay.flush_once()
                    continue

                if not scheduled_any:
                    deadlock = await self._reservations.detect_wait_cycles(run_id)
                    if deadlock.has_cycle:
                        await self._repo.emit_event(
                            run_id=run_id,
                            event_type="run.deadlock_detected",
                            subject_id=run_id,
                            payload=deadlock.model_dump(mode="json"),
                        )
                        await self._relay.flush_once()
                    return merge_outcomes
        finally:
            for aw in active_workers.values():
                if not aw.done():
                    aw.cancel()
            await self._reservations.stop_heartbeat(heartbeat)
