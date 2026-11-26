"""WebSocket `/ws/runs/{run_id}?after_seq=` endpoint with monotonic outbox replay (A7-5, A8-4)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import cast

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from foundry.api.state import DaemonState

ws_router = APIRouter(tags=["websocket"])


@ws_router.websocket("/ws/runs/{run_id}")
async def stream_run_events(
    websocket: WebSocket,
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
) -> None:
    """Replay committed outbox events (`publish_seq > after_seq`) then stream live."""
    state = cast(DaemonState, websocket.app.state.daemon)
    await websocket.accept()

    # Subscribe to live queue BEFORE querying DB so events published during replay are buffered
    live_queue = state.dispatcher.subscribe_live(run_id)
    last_sent_seq = after_seq

    try:
        # Flush any newly committed outbox rows synchronously if running in-process
        await state.relay.flush_once()

        missed = await state.repo.list_events_after(after_seq, run_id=run_id)
        missed.sort(key=lambda ev: int(ev.publish_seq or 0))
        for ev in missed:
            if ev.publish_seq is not None and ev.publish_seq > last_sent_seq:
                await websocket.send_json(ev.model_dump(mode="json"))
                last_sent_seq = ev.publish_seq

        async def _drain_live() -> None:
            nonlocal last_sent_seq
            while True:
                ev = await live_queue.get()
                if ev.publish_seq is not None and ev.publish_seq > last_sent_seq:
                    await websocket.send_json(ev.model_dump(mode="json"))
                    last_sent_seq = ev.publish_seq

        async def _watch_client() -> None:
            try:
                while True:
                    msg = await websocket.receive_text()
                    if msg.strip().lower() == "ping":
                        await websocket.send_json({"type": "pong", "after_seq": last_sent_seq})
            except WebSocketDisconnect:
                return

        sender_task = asyncio.create_task(_drain_live())
        receiver_task = asyncio.create_task(_watch_client())
        try:
            done, pending = await asyncio.wait(
                {sender_task, receiver_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for pending_task in pending:
                pending_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending_task
            for finished_task in done:
                exc = finished_task.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    raise exc
        except WebSocketDisconnect:
            return
    finally:
        state.dispatcher.unsubscribe_live(run_id, live_queue)
