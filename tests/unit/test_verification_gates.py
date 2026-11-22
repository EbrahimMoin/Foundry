"""Unit tests for GateRunner (G1/G2), pinned pointers, OpenAPI diff, test-IDs, and secret scans."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from foundry.agents.single.runner import ReplayMockSandboxProvider
from foundry.sandbox import ExecPriority, Sandbox, sync_worktree_to_workspace
from foundry.security import HardenedGit, SymlinkViolationError
from foundry.stack_packs import FastAPIReactPostgresPack
from foundry.verification import (
    GateName,
    GateRunner,
    check_pinned_pointers,
    check_pinned_testids,
    diff_openapi_breaking,
    extract_pinned_pointers,
    load_exported_openapi,
    scan_workspace_secrets,
)


def _sample_pinned_spec() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                "ItemCreate": {
                    "type": "object",
                    "required": ["url", "title"],
                    "properties": {"url": {"type": "string"}, "title": {"type": "string"}},
                },
                "ItemOut": {
                    "type": "object",
                    "required": ["id", "url", "title"],
                    "properties": {
                        "id": {"type": "string"},
                        "url": {"type": "string"},
                        "title": {"type": "string"},
                    },
                },
            }
        },
        "paths": {
            "/api/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ItemCreate"}
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ItemOut"}
                                }
                            },
                        }
                    },
                }
            }
        },
    }


def test_pinned_pointers_and_breaking_diff_checks() -> None:
    pinned = _sample_pinned_spec()
    ptrs = extract_pinned_pointers(pinned)
    assert "/paths/~1api~1items/post" in ptrs
    assert "/paths/~1api~1items/post/responses/201/required/id" in ptrs

    # Identical exported schema passes cleanly
    assert check_pinned_pointers(pinned, pinned) == []
    assert diff_openapi_breaking(pinned, pinned) == []

    # Removing required response field or adding newly required request field triggers violations
    broken = _sample_pinned_spec()
    broken["components"]["schemas"]["ItemOut"]["properties"].pop("title")
    broken["components"]["schemas"]["ItemCreate"]["required"].append("extra_required")
    broken["components"]["schemas"]["ItemCreate"]["properties"]["extra_required"] = {
        "type": "string"
    }

    pinned_viols = check_pinned_pointers(pinned, broken)
    assert len(pinned_viols) == 1
    assert pinned_viols[0].kind == "pinned_violation"
    assert "title" in pinned_viols[0].message

    diff_viols = diff_openapi_breaking(pinned, broken)
    kinds_msgs = [(v.kind, v.message) for v in diff_viols]
    assert any(k == "breaking_change" and "extra_required" in m for k, m in kinds_msgs)
    assert any(k == "breaking_change" and "title" in m for k, m in kinds_msgs)


def test_load_exported_openapi_and_testid_block_symlinks(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".foundry_tmp").mkdir(parents=True)
    (ws / "frontend" / "src").mkdir(parents=True)
    (ws / ".foundry_tmp" / "openapi.json").write_text(
        json.dumps({"openapi": "3.1.0", "paths": {}}),
        encoding="utf-8",
    )
    (ws / "frontend" / "src" / "App.tsx").write_text(
        '<div data-testid="bookmark-form" />',
        encoding="utf-8",
    )

    loaded = load_exported_openapi(ws)
    assert "paths" in loaded
    assert check_pinned_testids(ws, ["bookmark-form"]) == []
    missing = check_pinned_testids(ws, ["bookmark-form", "missing-id"])
    assert len(missing) == 1
    assert missing[0].kind == "testid_missing"

    # Planting a symlink anywhere inside workspace causes deterministic rejection before reads
    os.symlink("/etc/passwd", ws / "frontend" / "src" / "leak")
    with pytest.raises(SymlinkViolationError):
        load_exported_openapi(ws)
    with pytest.raises(SymlinkViolationError):
        check_pinned_testids(ws, ["bookmark-form"])


def test_scan_workspace_secrets_detects_tokens(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ok.py").write_text("value = 'clean'\n", encoding="utf-8")
    assert scan_workspace_secrets(ws) == []

    fake_secret = "sk-ant-" + ("A" * 24)
    (ws / "oops.py").write_text(f"API_KEY = '{fake_secret}'\n", encoding="utf-8")
    viols = scan_workspace_secrets(ws)
    assert len(viols) == 1
    assert viols[0].kind == "secret_leak"
    assert viols[0].pointer == "oops.py"


@pytest.mark.asyncio
async def test_gate_runner_g1_vs_g2_schemathesis_blocking_and_priority(tmp_path: Path) -> None:
    pack = FastAPIReactPostgresPack()
    git = HardenedGit()
    repo = tmp_path / "repo"
    await git.init_repo(repo)
    pack.scaffold(repo, "DemoApp")
    await git.commit(
        repo,
        "chore: wave0",
        ["backend/", "frontend/", "contracts/", ".spectral.yaml", "e2e/", "infra/", ".github/"],
        allow_infra=True,
    )
    wt = tmp_path / "wt"
    await git.create_worktree(repo, "build/demo", "main", wt)
    ws = tmp_path / "ws"
    sync_worktree_to_workspace(wt, ws)

    sbx = Sandbox(task_id="t_g1_g2", container_id="cid", workspace_dir=ws, image=pack.sandbox_image)
    provider = ReplayMockSandboxProvider(failing_commands={"schemathesis"})

    gates = GateRunner(
        provider,
        sbx,
        stack_pack=pack,
        git=git,
        worktree_dir=wt,
        write_set=["backend/", "frontend/", "contracts/"],
        run_schemathesis=True,
    )

    # G1 marks Schemathesis non-blocking (advisory)
    g1_report = await gates.run_g1()
    assert g1_report.gate == GateName.G1
    assert g1_report.passed is True
    assert any(
        c.name == "schemathesis" and not c.blocking and not c.passed for c in g1_report.checks
    )

    # G2 marks Schemathesis blocking
    g2_report = await gates.run_g2()
    assert g2_report.gate == GateName.G2
    assert g2_report.passed is False
    assert any(c.name == "schemathesis" and c.blocking and not c.passed for c in g2_report.checks)
    assert "Gate G2 FAILED" in g2_report.to_feedback_prompt()
    assert ExecPriority.IMPLEMENT_G1_G2 == 2
