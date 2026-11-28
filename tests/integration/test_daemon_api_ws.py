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
    default_foundry_config,
    default_models_config,
    default_project_config,
    save_foundry_config,
    save_models_config,
    save_project_config,
)
from foundry.config.schemas import SecretsConfig
from foundry.decisions.service import DecisionService
from foundry.security.vault import CredentialVault


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


@pytest.mark.asyncio
async def test_chat_sessions_api_key_vars_and_three_layer_agents(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    proj_dir = tmp_path / "proj"
    vault_file = tmp_path / "vault" / "credentials.enc"

    global_cfg = default_foundry_config().model_copy(
        update={
            "secrets": SecretsConfig(
                backend_order=["encrypted_vault"],
                vault_path=str(vault_file),
            )
        }
    )
    save_foundry_config(global_cfg, cfg_dir / "foundry.yaml")
    save_models_config(default_models_config(), cfg_dir / "models.yaml")
    save_project_config(
        default_project_config(name="clean_app"),
        proj_dir / ".foundry" / "project.yaml",
    )

    repo = InMemoryBlackboardRepository()
    dispatcher = EventDispatcher(repo)
    relay = EventRelay(repo, dispatcher)
    decisions = DecisionService(repo)
    state = DaemonState(
        repo=repo,
        dispatcher=dispatcher,
        relay=relay,
        decisions=decisions,
        config_dir=cfg_dir,
        project_dir=proj_dir,
    )
    app = create_app(state)

    with TestClient(app) as client:
        # 1. Vault unlock + named API key variable storage (zero plaintext exposure)
        initial_keys = client.get("/api/settings/keys").json()
        assert initial_keys["backend"] == "encrypted_vault"
        assert initial_keys["vault_unlocked"] is False

        unlock_resp = client.post(
            "/api/settings/vault/unlock",
            json={"passphrase": "test-passphrase-123"},
        )
        assert unlock_resp.status_code == 200
        assert unlock_resp.json()["vault_unlocked"] is True

        secret_val = "sk-foundry-super-secret-value-9988"
        key_resp = client.post(
            "/api/settings/keys",
            json={
                "name": "api key 1",
                "secret": secret_val,
                "provider_hint": "openai",
            },
        )
        assert key_resp.status_code == 200
        key_data = key_resp.json()
        assert key_data["name"] == "API_KEY_1"
        assert key_data["key_ref"] == "vault:var_api_key_1"
        assert secret_val not in key_data["masked_preview"]
        assert (cfg_dir / "api_keys.yaml").exists()
        assert secret_val not in (cfg_dir / "api_keys.yaml").read_text(encoding="utf-8")

        vault = CredentialVault(
            vault_path=vault_file,
            passphrase="test-passphrase-123",
            backend_order=("encrypted_vault",),
        )
        assert vault.get_secret("vault:var_api_key_1", allow_env_fallback=False) == secret_val

        # 2. 3-Layer Agent Roster CRUD + parent & key binding
        roster = client.get("/api/settings/agents").json()
        assert len(roster["agents"]) == 7

        add_l2 = client.post(
            "/api/settings/agents",
            json={
                "name": "Security Lead",
                "layer": 2,
                "role": "lead",
                "domain": "security",
                "parent_id": "orch_primary",
                "model_profile": "@lead",
                "api_key_var": "API_KEY_1",
            },
        )
        assert add_l2.status_code == 200
        sec_lead = next(a for a in add_l2.json()["agents"] if a["name"] == "Security Lead")
        assert sec_lead["api_key_var"] == "API_KEY_1"

        add_l3 = client.post(
            "/api/settings/agents",
            json={
                "name": "Security Implementer",
                "layer": 3,
                "role": "implementer",
                "domain": "security",
                "parent_id": sec_lead["id"],
                "model_profile": "@coder",
                "api_key_var": "API_KEY_1",
            },
        )
        assert add_l3.status_code == 200
        sec_impl = next(a for a in add_l3.json()["agents"] if a["name"] == "Security Implementer")

        # Removing Layer 2 parent cleanly detaches child parent_id
        del_l2 = client.delete(f"/api/settings/agents/{sec_lead['id']}")
        assert del_l2.status_code == 200
        updated_child = next(a for a in del_l2.json()["agents"] if a["id"] == sec_impl["id"])
        assert updated_child["parent_id"] is None

        # 3. Clean chat/run creation + messages + active run agent add/remove
        created = client.post(
            "/api/runs",
            json={"prompt": "Build an invoice tracker with approvals", "project_id": "clean_app"},
        )
        assert created.status_code == 200
        created_json = created.json()
        run_id = created_json["run"]["id"]
        assert created_json["chat"]["is_chat_session"] is True
        assert "Build an invoice tracker" in created_json["chat"]["title"]
        assert len(created_json["messages"]) == 2
        assert len(created_json["agents"]) == 8

        chats = client.get("/api/chats").json()
        assert len(chats) == 1
        assert chats[0]["run_id"] == run_id

        with client.websocket_connect(f"/ws/runs/{run_id}?after_seq=0") as ws:
            # Consume initial replay frames
            _ = ws.receive_json()
            _ = ws.receive_json()

            post_msg = client.post(
                f"/api/runs/{run_id}/messages",
                json={"body": "Use PostgreSQL 16 and strict OpenAPI contracts", "from_id": "human"},
            )
            assert post_msg.status_code == 200
            ev = ws.receive_json()
            assert ev["type"] == "chat.message_posted"

        msgs = client.get(f"/api/runs/{run_id}/messages").json()
        assert len(msgs) == 3

        # Add & remove agent on active run
        added_live = client.post(
            f"/api/runs/{run_id}/agents",
            json={"role": "implementer", "domain": "billing", "model_profile": "@coder_plus"},
        )
        assert added_live.status_code == 200
        live_agent_id = added_live.json()["id"]
        removed_live = client.delete(f"/api/runs/{run_id}/agents/{live_agent_id}")
        assert removed_live.status_code == 200
        assert removed_live.json()["status"] == "removed"

        # Deleting API_KEY_1 clears api_key_var binding on remaining agent slots
        del_key = client.delete("/api/settings/keys/API_KEY_1")
        assert del_key.status_code == 200
        roster_after = client.get("/api/settings/agents").json()
        sec_impl_after = next(a for a in roster_after["agents"] if a["id"] == sec_impl["id"])
        assert sec_impl_after["api_key_var"] is None
        assert sec_impl_after["auth_source"] == "profile_default"


@pytest.mark.asyncio
async def test_catalog_key_filtered_models_purge_and_subscription_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_dir = tmp_path / "cfg"
    proj_dir = tmp_path / "proj"
    vault_file = tmp_path / "vault" / "credentials.enc"

    global_cfg = default_foundry_config().model_copy(
        update={
            "secrets": SecretsConfig(
                backend_order=["encrypted_vault"],
                vault_path=str(vault_file),
            )
        }
    )
    save_foundry_config(global_cfg, cfg_dir / "foundry.yaml")
    save_models_config(default_models_config(), cfg_dir / "models.yaml")
    save_project_config(
        default_project_config(name="my_clean_project"),
        proj_dir / ".foundry" / "project.yaml",
    )

    repo = InMemoryBlackboardRepository()
    legacy_test_run = await repo.create_run(project_id="live_p5_proj", budget_usd=5.0)
    dispatcher = EventDispatcher(repo)
    relay = EventRelay(repo, dispatcher)
    decisions = DecisionService(repo)
    state = DaemonState(
        repo=repo,
        dispatcher=dispatcher,
        relay=relay,
        decisions=decisions,
        config_dir=cfg_dir,
        project_dir=proj_dir,
    )
    app = create_app(state)

    with TestClient(app) as client:
        # 1. Workspace project comes first even when test projects exist
        projects = client.get("/api/projects").json()
        assert projects[0]["name"] == "my_clean_project"

        # 2. Create a clean human chat alongside the non-chat legacy run, then purge test runs
        created = client.post(
            "/api/runs",
            json={"prompt": "Build a personal notes workspace"},
        )
        assert created.status_code == 200
        chat_run_id = created.json()["run"]["id"]

        purged = client.post("/api/chats/purge-test-runs")
        assert purged.status_code == 200
        assert purged.json()["deleted_count"] == 1
        assert purged.json()["deleted_run_ids"] == [legacy_test_run.id]

        remaining_chats = client.get("/api/chats").json()
        assert [c["run_id"] for c in remaining_chats] == [chat_run_id]

        # Delete explicit chat run
        deleted_chat = client.delete(f"/api/runs/{chat_run_id}")
        assert deleted_chat.status_code == 200
        assert client.get("/api/chats").json() == []

        # 3. Provider catalog has unambiguous labels & API key returns provider_label
        cat_resp = client.get("/api/settings/catalog")
        assert cat_resp.status_code == 200
        labels = {p["id"]: p["label"] for p in cat_resp.json()["providers"]}
        assert labels["gemini"] == "Google Gemini"
        assert labels["openai"] == "OpenAI"
        assert labels["openai_codex_cli"] == "OpenAI Subscription (Codex CLI)"

        client.post("/api/settings/vault/unlock", json={"passphrase": "pass-123"})
        gem_key = client.post(
            "/api/settings/keys",
            json={
                "name": "GEM_KEY_1",
                "secret": "AIza-test-secret-0001",
                "provider": "gemini",
            },
        )
        assert gem_key.status_code == 200
        assert gem_key.json()["provider"] == "gemini"
        assert gem_key.json()["provider_label"] == "Google Gemini"

        # 4. Model profile CRUD enforces provider-matched models (rejects Grok on Gemini key)
        bad_prof = client.put(
            "/api/settings/models/profiles",
            json={
                "profile_name": "@gem_bad",
                "auth_source": "api_key",
                "api_key_var": "GEM_KEY_1",
                "model": "grok-3-mini",
            },
        )
        assert bad_prof.status_code == 400

        good_prof = client.put(
            "/api/settings/models/profiles",
            json={
                "profile_name": "@gem_custom",
                "auth_source": "api_key",
                "api_key_var": "GEM_KEY_1",
                "model": "gemini-2.5-pro",
                "input_per_m": 2.0,
                "output_per_m": 12.0,
            },
        )
        assert good_prof.status_code == 200
        assert good_prof.json()["profiles"]["@gem_custom"]["model"] == "gemini-2.5-pro"

        del_prof = client.delete("/api/settings/models/profiles/@gem_custom")
        assert del_prof.status_code == 200
        assert "@gem_custom" not in del_prof.json()["profiles"]

        # 5. Agent slot enforces provider-matched models
        bad_agent = client.post(
            "/api/settings/agents",
            json={
                "name": "Gem Planner",
                "layer": 1,
                "role": "orchestrator",
                "model_profile": "@planner",
                "auth_source": "api_key",
                "api_key_var": "GEM_KEY_1",
                "model_name": "gpt-5",
            },
        )
        assert bad_agent.status_code == 400

        good_agent = client.post(
            "/api/settings/agents",
            json={
                "name": "Gem Planner",
                "layer": 1,
                "role": "orchestrator",
                "model_profile": "@planner",
                "auth_source": "api_key",
                "api_key_var": "GEM_KEY_1",
                "model_name": "gemini-2.5-pro",
            },
        )
        assert good_agent.status_code == 200

        # 6. OpenAI Subscription CLI status + opt-in toggle + mocked single-shot test
        monkeypatch.setattr(
            "foundry.gateway.transports.openai_codex_cli.shutil.which",
            lambda name: "/usr/local/bin/codex",
        )

        class _FakeVersionProc:
            returncode = 0

            async def communicate(self, stdin: bytes | None = None) -> tuple[bytes, bytes]:
                if stdin is None:
                    return b"codex-cli 0.25.0\n", b""
                return b"OK\n", b""

        async def _fake_exec(*cmd: str, **_: object) -> _FakeVersionProc:
            return _FakeVersionProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

        sub_initial = client.get("/api/settings/subscriptions/openai").json()
        assert sub_initial["enabled"] is False
        assert sub_initial["cli_installed"] is True
        assert sub_initial["supports_tool_calling"] is False

        # Blocked before opt-in
        blocked_test = client.post(
            "/api/settings/test-connection",
            json={"auth_source": "openai_subscription", "model": "gpt-5"},
        ).json()
        assert blocked_test["ok"] is False
        assert "disabled" in (blocked_test["error"] or "").lower()

        # Enable opt-in & test passes via single-shot mock
        sub_enabled = client.put(
            "/api/settings/subscriptions/openai",
            json={"enabled": True},
        ).json()
        assert sub_enabled["enabled"] is True

        ok_test = client.post(
            "/api/settings/test-connection",
            json={"auth_source": "openai_subscription", "model": "gpt-5"},
        ).json()
        assert ok_test["ok"] is True
        assert ok_test["reply_preview"] == "OK"
