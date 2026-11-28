"""Integration tests for FastAPI Daemon API & gapless WebSocket `?after_seq=` stream."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from foundry.api import DaemonState, create_app
from foundry.blackboard.events import EventDispatcher, EventRelay
from foundry.blackboard.models import TaskStatus
from foundry.blackboard.repository import InMemoryBlackboardRepository
from foundry.config.loader import (
    default_models_config,
    default_project_config,
    save_models_config,
    save_project_config,
)
from foundry.decisions.service import DecisionService


@pytest.mark.asyncio
async def test_daemon_api_endpoints_and_ws_after_seq(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    proj_dir = tmp_path / "proj"
    dash_dir = tmp_path / "dashboard" / "dist"
    dash_dir.mkdir(parents=True, exist_ok=True)
    (dash_dir / "index.html").write_text("<html><body>Foundry v0.5</body></html>", encoding="utf-8")

    save_models_config(default_models_config(), cfg_dir / "models.yaml")
    save_project_config(
        default_project_config(name="bookmarks"), proj_dir / ".foundry" / "project.yaml"
    )

    repo = InMemoryBlackboardRepository()
    dispatcher = EventDispatcher(repo)
    relay = EventRelay(repo, dispatcher)
    decisions = DecisionService(repo)

    run = await repo.create_run(project_id="bookmarks", budget_usd=5.0, phase="building")
    agent = await repo.register_agent(
        run_id=run.id,
        role="implementer",
        domain="backend",
        model_profile="@coder",
        graph_thread_id=f"{run.id}:task:t1:1",
    )
    task = await repo.create_task(
        task_id="t1",
        run_id=run.id,
        wave=2,
        domain="backend",
        feature="bookmarks",
        kind="build",
        title="Bookmarks endpoint",
        description="POST/GET bookmarks",
        write_set=["backend/app/features/bookmarks/"],
        budget_implement_usd=0.60,
        budget_review_usd=0.15,
        status=TaskStatus.IN_PROGRESS,
    )
    await repo.acquire_write_reservations(run.id, task.id, ["backend/app/features/bookmarks/"])
    ctr, _ = await repo.save_contract_version(
        run_id=run.id,
        name="api",
        domain="backend",
        kind="openapi",
        body={"openapi": "3.1.0", "paths": {"/api/bookmarks": {"get": {}}}},
    )
    dec = await decisions.get_or_create(
        run_id=run.id,
        idempotency_key="dec_g6_1",
        category="strategic",
        title="Approve deployment",
        context="Promote v1",
        options=["approve_v1", "hold"],
        recommendation="approve_v1",
        confidence=0.4,
    )
    await repo.upsert_feature(
        run.id,
        "bookmarks",
        status="verified",
        spent_delta_usd=0.05,
        dispute_rounds_delta=1,
    )
    await repo.record_llm_call(
        run_id=run.id,
        agent_id=agent.id,
        task_id=task.id,
        provider="gemini_paid",
        model="gemini-2.5-flash-lite",
        profile="@coder",
        transport="google_genai",
        billing_mode="paid_api",
        input_tokens=1000,
        cached_tokens=400,
        output_tokens=200,
        cost_usd=0.002,
        est_value_usd=0.002,
    )
    await relay.flush_once()

    state = DaemonState(
        repo=repo,
        dispatcher=dispatcher,
        relay=relay,
        decisions=decisions,
        config_dir=cfg_dir,
        project_dir=proj_dir,
        dashboard_dir=dash_dir,
    )
    app = create_app(state)

    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert "Foundry v0.5" in client.get("/").text

        projects = client.get("/api/projects").json()
        assert projects[0]["name"] == "bookmarks"
        assert projects[0]["run_count"] == 1

        runs = client.get("/api/runs").json()
        assert len(runs) == 1
        assert client.get(f"/api/runs/{run.id}").json()["phase"] == "building"

        assert client.post(f"/api/runs/{run.id}/pause").json()["phase"] == "paused"
        assert client.post(f"/api/runs/{run.id}/resume").json()["phase"] == "building"

        assert len(client.get(f"/api/runs/{run.id}/agents").json()) == 1
        assert len(client.get(f"/api/runs/{run.id}/tasks").json()) == 1
        detail = client.get(f"/api/tasks/{task.id}").json()
        assert detail["write_set"] == ["backend/app/features/bookmarks/"]
        assert len(detail["reservations"]) == 1

        assert len(client.get(f"/api/runs/{run.id}/reservations").json()) == 1
        assert len(client.get(f"/api/runs/{run.id}/contracts").json()) == 1
        versions = client.get(f"/api/contracts/{ctr.id}/versions").json()
        assert len(versions["versions"]) == 1

        pending = client.get(f"/api/runs/{run.id}/decisions?status=pending").json()
        assert len(pending) == 1
        resolved = client.post(
            f"/api/decisions/{dec.id}/resolve",
            json={"action": "approve", "choice": "approve_v1", "decided_by": "human"},
        ).json()
        assert resolved["status"] == "resolved"

        costs = client.get(f"/api/runs/{run.id}/costs").json()
        assert costs["call_count"] == 1
        assert costs["cache_hit_rate"] == 0.4

        metrics = client.get(f"/api/runs/{run.id}/metrics").json()
        assert metrics["dispute_rounds_total"] == 1
        assert metrics["active_reservations"] == 1

        # Settings endpoints
        assert "gemini_paid" in client.get("/api/settings/providers").json()
        updated_models = client.put(
            "/api/settings/models/assignments",
            json={"roles": {"implementer": "@coder_plus"}},
        ).json()
        assert updated_models["assignments"]["roles"]["implementer"] == "@coder_plus"

        updated_autonomy = client.put(
            "/api/settings/autonomy",
            json={"preset": "architect"},
        ).json()
        assert updated_autonomy["preset"] == "architect"

        updated_dp = client.put(
            "/api/settings/data-policy",
            json={"allow_free_tier": True, "allow_trace_export": False},
        ).json()
        assert updated_dp["allow_free_tier"] is True
        partial_dp = client.put(
            "/api/settings/data-policy",
            json={"allow_trace_export": True},
        ).json()
        assert partial_dp["allow_free_tier"] is True
        assert partial_dp["allow_trace_export"] is True

        runtime_cfg = client.get("/api/settings/runtime").json()
        assert runtime_cfg["server"]["port"] == 4200
        assert runtime_cfg["runtime"]["max_heavy_exec_slots"] == 2
        assert "encrypted_vault" in runtime_cfg["secrets_backends"]

        # Operator notes / run chat persistence
        assert client.get(f"/api/runs/{run.id}/notes").json() == []
        created_note = client.post(
            f"/api/runs/{run.id}/notes",
            json={"body": "Keep Wave 1 minimal", "kind": "note"},
        )
        assert created_note.status_code == 200
        assert created_note.json()["body"] == "Keep Wave 1 minimal"
        created_gotcha = client.post(
            f"/api/runs/{run.id}/chat",
            json={"body": "Avoid breaking cursor schema", "kind": "gotcha"},
        )
        assert created_gotcha.status_code == 200
        notes_list = client.get(f"/api/runs/{run.id}/notes").json()
        assert len(notes_list) == 2

        preview = client.get(f"/api/preview/{run.id}/url?env=local").json()
        assert preview["available"] is True

        # WebSocket ?after_seq= replay + live monotonic stream
        all_events = await repo.list_events_after(0, run_id=run.id)
        assert len(all_events) >= 3
        cursor_seq = all_events[1].publish_seq
        assert cursor_seq is not None

        with client.websocket_connect(f"/ws/runs/{run.id}?after_seq={cursor_seq}") as ws:
            replayed_msgs = [ws.receive_json() for _ in range(len(all_events) - 2)]
            seqs = [m["publish_seq"] for m in replayed_msgs]
            assert all(s > cursor_seq for s in seqs)
            assert seqs == sorted(seqs)

            # New note triggers live `run.note_logged` event over WebSocket
            note_resp = client.post(
                f"/api/runs/{run.id}/notes",
                json={"body": "Live note event check", "kind": "note"},
            )
            assert note_resp.status_code == 200
            live_note_ev = ws.receive_json()
            assert live_note_ev["type"] == "run.note_logged"
            assert live_note_ev["publish_seq"] > seqs[-1]

            # Stop run triggers new committed + published outbox event streamed live
            stop_resp = client.post(f"/api/runs/{run.id}/stop")
            assert stop_resp.status_code == 200
            live_1 = ws.receive_json()
            assert live_1["publish_seq"] > live_note_ev["publish_seq"]
