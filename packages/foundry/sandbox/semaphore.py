"""Priority-aware HeavyExecSemaphore capping concurrent RAM-heavy container checks."""

from __future__ import annotations

import asyncio
import heapq
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from foundry.sandbox.protocol import ExecPriority


class HeavyExecSemaphore:
    """2-slot priority semaphore ensuring G5 Merge Queue preempts waiting G3/G2/G1 checks."""

    def __init__(self, max_slots: int = 2) -> None:
        if max_slots < 1:
            raise ValueError("max_slots must be >= 1")
        self.max_slots = max_slots
        self.active_slots = 0
        self.total_wait_ms = 0
        self._seq = 0
        self._condition = asyncio.Condition()
        self._waiters: list[tuple[int, int, asyncio.Event]] = []

    @asynccontextmanager
    async def acquire(
        self,
        priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
    ) -> AsyncIterator[int]:
        """Acquire a heavy-execution slot ordered by priority, yielding wait time in ms."""
        wait_start = time.perf_counter()
        async with self._condition:
            if self.active_slots < self.max_slots and not self._waiters:
                self.active_slots += 1
                waiter: tuple[int, int, asyncio.Event] | None = None
            else:
                event = asyncio.Event()
                self._seq += 1
                waiter = (int(priority), self._seq, event)
                heapq.heappush(self._waiters, waiter)
                try:
                    while True:
                        if self.active_slots < self.max_slots and self._waiters[0] is waiter:
                            heapq.heappop(self._waiters)
                            self.active_slots += 1
                            break
                        await self._condition.wait()
                except BaseException:
                    if waiter in self._waiters:
                        self._waiters.remove(waiter)
                        heapq.heapify(self._waiters)
                        self._condition.notify_all()
                    raise

        wait_ms = int((time.perf_counter() - wait_start) * 1000)
        self.total_wait_ms += wait_ms
        try:
            yield wait_ms
        finally:
            async with self._condition:
                self.active_slots -= 1
                self._condition.notify_all()
