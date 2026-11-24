"""Prefix-aware write-set reservations, task-scoped heartbeats, and deadlock detection."""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import Sequence

from pydantic import BaseModel, Field

from foundry.blackboard.models import (
    ReservationConflict,
    TaskStatus,
    WriteReservationRecord,
)
from foundry.blackboard.repository import BlackboardRepository
from foundry.security.workspace_guard import normalize_repo_path, paths_overlap

ACTIVE_HEARTBEAT_STATES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.IN_PROGRESS,
        TaskStatus.IN_REVIEW,
        TaskStatus.VERIFIED,
        TaskStatus.QUEUED,
    }
)

RELEASE_STATES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.MERGED,
        TaskStatus.CANCELLED,
        TaskStatus.ESCALATED,
    }
)


class DeadlockReport(BaseModel):
    """Result of scanning the run's wait-for graph (dependencies + reservation waits + frozen)."""

    has_cycle: bool = False
    cycle_task_ids: list[str] = Field(default_factory=list)
    edges: list[tuple[str, str, str]] = Field(default_factory=list)


class WriteReservationManager:
    """Manages advisory-locked prefix reservations, 60s task heartbeats & wait-for cycles."""

    def __init__(
        self,
        repo: BlackboardRepository,
        *,
        default_ttl_seconds: int = 600,
        heartbeat_interval_seconds: float = 60.0,
    ) -> None:
        self._repo = repo
        self.default_ttl_seconds = default_ttl_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

    async def acquire(
        self,
        run_id: str,
        task_id: str,
        paths: Sequence[str],
        *,
        ttl_seconds: int | None = None,
    ) -> tuple[list[WriteReservationRecord], list[ReservationConflict]]:
        """Atomically acquire prefix-checked reservations for a task's normalized write set."""
        normalized = [normalize_repo_path(p, allow_directory_slash=True) for p in paths]
        return await self._repo.acquire_write_reservations(
            run_id,
            task_id,
            normalized,
            ttl_seconds=ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds,
        )

    async def renew_active_for_run(
        self,
        run_id: str,
        *,
        ttl_seconds: int | None = None,
    ) -> int:
        """Renew active reservation TTLs for every task holding an active pipeline lock."""
        tasks = await self._repo.list_tasks(run_id)
        active_task_ids = [t.id for t in tasks if t.status in ACTIVE_HEARTBEAT_STATES]
        if not active_task_ids:
            return 0
        return await self._repo.renew_write_reservations(
            run_id,
            task_ids=active_task_ids,
            ttl_seconds=ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds,
        )

    async def release(
        self,
        run_id: str,
        task_id: str,
        *,
        reason: str = "released",
    ) -> int:
        """Release all active reservations held by a task (at MERGED, CANCELLED, or ESCALATED)."""
        return await self._repo.release_write_reservations(run_id, task_id, reason=reason)

    async def list_active(
        self,
        run_id: str,
        *,
        include_expired: bool = False,
    ) -> list[WriteReservationRecord]:
        """List active write-set reservations for a run."""
        return await self._repo.list_active_reservations(run_id, include_expired=include_expired)

    def start_heartbeat(
        self,
        run_id: str,
        *,
        interval_s: float | None = None,
        ttl_seconds: int | None = None,
    ) -> asyncio.Task[None]:
        """Start a cancellable background heartbeat renewing active reservations every 60s."""
        poll_interval = interval_s if interval_s is not None else self.heartbeat_interval_seconds

        async def _loop() -> None:
            while True:
                await asyncio.sleep(poll_interval)
                await self.renew_active_for_run(run_id, ttl_seconds=ttl_seconds)

        return asyncio.create_task(_loop(), name=f"foundry-res-heartbeat:{run_id}")

    @staticmethod
    async def stop_heartbeat(task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def detect_wait_cycles(self, run_id: str) -> DeadlockReport:
        """Detect cycles across dependency waits, reservation waits, and frozen tasks."""
        tasks = await self._repo.list_tasks(run_id)
        by_id = {t.id: t for t in tasks}
        active_res = await self._repo.list_active_reservations(run_id)
        holders: dict[str, list[str]] = defaultdict(list)
        for rec in active_res:
            holders[rec.task_id].append(rec.path)

        adj: dict[str, list[str]] = defaultdict(list)
        edges: list[tuple[str, str, str]] = []

        for task in tasks:
            if task.status in {TaskStatus.BLOCKED, TaskStatus.FROZEN}:
                for dep_id in await self._repo.get_task_dependencies(task.id):
                    dep_task = by_id.get(dep_id)
                    if dep_task is not None and dep_task.status != TaskStatus.MERGED:
                        adj[task.id].append(dep_id)
                        edges.append(
                            (
                                task.id,
                                dep_id,
                                "frozen" if task.status == TaskStatus.FROZEN else "dependency",
                            )
                        )
            if task.status == TaskStatus.READY:
                ws = await self._repo.get_task_write_sets(task.id)
                for holder_id, held_paths in holders.items():
                    if holder_id != task.id and paths_overlap(ws, held_paths):
                        adj[task.id].append(holder_id)
                        edges.append((task.id, holder_id, "reservation"))

        # Standard 3-color DFS to extract the first deterministic wait-for cycle
        color: dict[str, int] = {}
        stack: list[str] = []
        cycle: list[str] = []

        def _dfs(node: str) -> bool:
            color[node] = 1
            stack.append(node)
            for nxt in sorted(adj.get(node, [])):
                state = color.get(nxt, 0)
                if state == 1:
                    idx = stack.index(nxt)
                    cycle.extend(stack[idx:])
                    return True
                if state == 0 and _dfs(nxt):
                    return True
            stack.pop()
            color[node] = 2
            return False

        for tid in sorted(adj):
            if color.get(tid, 0) == 0 and _dfs(tid):
                return DeadlockReport(has_cycle=True, cycle_task_ids=cycle, edges=edges)

        return DeadlockReport(has_cycle=False, cycle_task_ids=[], edges=edges)
