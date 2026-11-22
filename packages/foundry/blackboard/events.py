"""Postgres-only LISTEN/NOTIFY outbox relay and crash-safe asyncio EventDispatcher (A7-5)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

from foundry.blackboard.dsn import to_asyncpg_dsn
from foundry.blackboard.models import BlackboardEvent
from foundry.blackboard.repository import BlackboardRepository

EventHandler = Callable[[BlackboardEvent], Awaitable[None]]


@dataclass
class _ConsumerRegistration:
    group: str
    handler: EventHandler
    event_types: frozenset[str] | None


class EventDispatcher:
    """In-process asyncio dispatcher with `processed_events` deduplication & crash replay."""

    def __init__(self, repo: BlackboardRepository) -> None:
        self._repo = repo
        self._consumers: list[_ConsumerRegistration] = []

    def subscribe(
        self,
        consumer_group: str,
        handler: EventHandler,
        *,
        event_types: Sequence[str] | None = None,
    ) -> None:
        filter_set = frozenset(event_types) if event_types is not None else None
        self._consumers.append(
            _ConsumerRegistration(
                group=consumer_group,
                handler=handler,
                event_types=filter_set,
            )
        )

    async def dispatch(self, event: BlackboardEvent) -> None:
        if event.publish_seq is None:
            return
        for consumer in self._consumers:
            if consumer.event_types is not None and event.type not in consumer.event_types:
                continue
            should_handle = await self._repo.mark_event_processed(
                consumer.group,
                event.publish_seq,
            )
            if should_handle:
                await consumer.handler(event)

    async def replay_missed(
        self,
        consumer_group: str,
        *,
        run_id: str | None = None,
    ) -> int:
        """Replay committed events strictly above `last_processed_seq(consumer_group)`."""
        last_seq = await self._repo.last_processed_seq(consumer_group)
        events = await self._repo.list_events_after(last_seq, run_id=run_id)
        replayed = 0
        for ev in events:
            if ev.publish_seq is None:
                continue
            for consumer in self._consumers:
                if consumer.group != consumer_group:
                    continue
                if consumer.event_types is not None and ev.type not in consumer.event_types:
                    continue
                if await self._repo.mark_event_processed(consumer.group, ev.publish_seq):
                    await consumer.handler(ev)
                    replayed += 1
        return replayed


class EventRelay:
    """Assigns monotonic `publish_seq` via Postgres advisory lock and notifies consumers."""

    def __init__(self, repo: BlackboardRepository, dispatcher: EventDispatcher) -> None:
        self._repo = repo
        self._dispatcher = dispatcher

    async def flush_once(self, *, limit: int = 500) -> list[BlackboardEvent]:
        """Publish all committed unpublished events in strict monotonic order and fan out."""
        published = await self._repo.publish_pending_events(limit=limit)
        for ev in published:
            await self._dispatcher.dispatch(ev)
        return published

    async def run_forever(
        self,
        stop_event: asyncio.Event,
        *,
        listen_dsn: str | None = None,
        channel: str = "events_new",
        poll_interval_s: float = 0.5,
    ) -> None:
        """Listen on Postgres `NOTIFY events_new` (with poll fallback) until `stop_event`."""
        wakeup = asyncio.Event()
        conn: asyncpg.Connection | None = None

        def _on_notify(*_args: Any) -> None:
            wakeup.set()

        if listen_dsn:
            conn = await asyncpg.connect(to_asyncpg_dsn(listen_dsn))
            await conn.add_listener(channel, _on_notify)

        try:
            await self.flush_once()
            while not stop_event.is_set():
                wakeup.clear()
                wait_task = asyncio.create_task(wakeup.wait())
                stop_task = asyncio.create_task(stop_event.wait())
                try:
                    await asyncio.wait(
                        {wait_task, stop_task},
                        timeout=poll_interval_s,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for pending_task in (wait_task, stop_task):
                        if not pending_task.done():
                            pending_task.cancel()
                await self.flush_once()
        finally:
            if conn is not None:
                try:
                    await conn.remove_listener(channel, _on_notify)
                finally:
                    await conn.close()
