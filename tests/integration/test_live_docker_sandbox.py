"""Live Docker Engine & Postgres integration tests (run in WSL2 with `foundry up`)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import asyncpg
import pytest
from bench.runner import B1BenchmarkRunner

from foundry.config import RuntimeConfig
from foundry.gateway import CassetteStore, MissPolicy
from foundry.sandbox import (
    ExecPriority,
    HeavyExecSemaphore,
    LocalDockerProvider,
    LogicalDBManager,
)
from foundry.stack_packs import FastAPIReactPostgresPack

_ADMIN_DSN = os.environ.get(
    "FOUNDRY_TEST_ADMIN_DSN",
    "postgresql://foundry:foundry_local_dev@127.0.0.1:5433/foundry_blackboard",
)


@pytest.mark.live_docker
@pytest.mark.asyncio
async def test_live_postgres_logical_db_isolation() -> None:
    mgr = LogicalDBManager(_ADMIN_DSN)
    await mgr.startup_cleanup()

    started = time.perf_counter()
    sbx_db = await mgr.create_sandbox_db("live_p2")
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0

    # Replace internal docker hostname/port with WSL2 localhost published port 5433
    host_db_url = sbx_db.database_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "@foundry-postgres:5432/", "@127.0.0.1:5433/"
    )
    blackboard_url = host_db_url.rsplit("/", 1)[0] + "/foundry_blackboard"

    try:
        conn = await asyncpg.connect(host_db_url)
        try:
            assert await conn.fetchval("SELECT 1") == 1
        finally:
            await conn.close()

        # Sandbox role MUST NOT be able to connect to foundry_blackboard (A7-3)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            bad_conn = await asyncpg.connect(blackboard_url)
            await bad_conn.close()
    finally:
        await mgr.drop_sandbox_db("live_p2")


@pytest.mark.live_docker
@pytest.mark.asyncio
async def test_live_docker_sandbox_hardening_and_gates(tmp_path: Path) -> None:
    pack = FastAPIReactPostgresPack()
    ws = tmp_path / "wave0_ws"
    pack.scaffold(ws, app_name="LiveSmoke")

    provider = LocalDockerProvider(RuntimeConfig(), HeavyExecSemaphore(2))
    sbx = await provider.create(
        task_id="live_hardening",
        image=pack.sandbox_image,
        workspace=ws,
        env={},
    )
    try:
        # 1. Zero git binary inside sandbox
        git_chk = await provider.exec(sbx, ["which", "git"])
        assert git_chk.exit_code != 0

        # 2. Read-only rootfs blocks writes outside /workspace and /tmp
        ro_chk = await provider.exec(sbx, ["touch", "/etc/evil"])
        assert ro_chk.exit_code != 0

        # 3. Default no-network blocks outbound internet
        net_chk = await provider.exec(
            sbx,
            [
                "python3",
                "-c",
                "import urllib.request; urllib.request.urlopen('https://pypi.org', timeout=2)",
            ],
            network=False,
        )
        assert net_chk.exit_code != 0

        # 4. In-container Wave 0 ruff & OpenAPI export work natively
        ruff_res = await provider.exec(
            sbx,
            ["ruff", "check", "backend/"],
            heavy=True,
            priority=ExecPriority.IMPLEMENT_G1_G2,
        )
        assert ruff_res.exit_code == 0, ruff_res.stdout + ruff_res.stderr
    finally:
        await provider.destroy(sbx)


@pytest.mark.live_docker
@pytest.mark.asyncio
async def test_live_docker_b1_s1_bookmarks_with_hidden_acceptance_suite(tmp_path: Path) -> None:
    spec_dir = await asyncio.to_thread(Path("bench/specs/s1_bookmarks").resolve)
    hidden_dir = await asyncio.to_thread(Path("bench/hidden_tests/s1_bookmarks").resolve)
    cassette_dir = await asyncio.to_thread(Path("tests/cassettes").resolve)
    cassettes = CassetteStore(cassette_dir, miss_policy=MissPolicy.STRICT)

    b1 = B1BenchmarkRunner(cassettes=cassettes)
    result = await b1.run(
        spec_dir=spec_dir,
        hidden_tests_dir=hidden_dir,
        out_dir=tmp_path / "live_b1_s1",
        budget_usd=1.50,
        run_hidden_tests=True,
        run_frontend=False,
        run_schemathesis=True,
    )

    assert result.status == "passed", result.error
    assert result.g2_passed is True, result.error
    assert result.hidden_api_ran is True
    assert result.hidden_api_total == 5
    assert result.hidden_api_passed == 5
    assert result.hidden_api_pass_rate == 1.0
    assert result.cost_usd == 0.0
