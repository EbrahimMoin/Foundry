"""Unit tests for HeavyExecSemaphore, .git-free WorkspaceSync, Docker, and LogicalDBManager."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from foundry.config import RuntimeConfig
from foundry.sandbox import (
    ExecPriority,
    HeavyExecSemaphore,
    LocalDockerProvider,
    LogicalDBError,
    LogicalDBManager,
    SandboxError,
    sanitize_task_db_name,
    sync_workspace_to_worktree,
    sync_worktree_to_workspace,
)
from foundry.security import HardenedGit, SymlinkViolationError, WriteSetViolationError


@pytest.mark.asyncio
async def test_heavy_exec_semaphore_caps_slots_and_prioritizes_g5() -> None:
    sem = HeavyExecSemaphore(max_slots=1)
    order: list[str] = []
    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()

    async def _holder() -> None:
        async with sem.acquire(ExecPriority.IMPLEMENT_G1_G2):
            order.append("holder_start")
            holder_entered.set()
            await release_holder.wait()
            order.append("holder_end")

    async def _waiter(label: str, prio: ExecPriority) -> None:
        async with sem.acquire(prio):
            order.append(label)

    t_hold = asyncio.create_task(_holder())
    await holder_entered.wait()

    t_impl = asyncio.create_task(_waiter("impl_g1", ExecPriority.IMPLEMENT_G1_G2))
    await asyncio.sleep(0)
    t_g3 = asyncio.create_task(_waiter("verify_g3", ExecPriority.VERIFY_G3))
    await asyncio.sleep(0)
    t_g5 = asyncio.create_task(_waiter("merge_g5", ExecPriority.MERGE_QUEUE_G5))
    await asyncio.sleep(0)

    assert sem.active_slots == 1
    release_holder.set()
    await asyncio.gather(t_hold, t_impl, t_g3, t_g5)

    assert order == ["holder_start", "holder_end", "merge_g5", "verify_g3", "impl_g1"]
    assert sem.active_slots == 0


@pytest.mark.asyncio
async def test_git_free_workspace_sync_blocks_symlinks_and_oob_writes(tmp_path: Path) -> None:
    git = HardenedGit()
    repo = tmp_path / "repo"
    await git.init_repo(repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    (repo / "backend" / "app" / "features" / "tasks").mkdir(parents=True)
    (repo / "backend" / "app" / "features" / "tasks" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    await git.commit(repo, "init", ["README.md", "backend/"], allow_infra=True)

    worktree = tmp_path / "wt"
    await git.create_worktree(repo, "task/t1-tasks", "main", worktree)
    workspace = tmp_path / "workspace"

    sync_worktree_to_workspace(worktree, workspace)
    assert (workspace / "README.md").exists()
    assert not (workspace / ".git").exists()

    # Creating a symlink inside container workspace fails pre-sync check
    os.symlink("/etc/passwd", workspace / "backend" / "app" / "features" / "tasks" / "evil")
    with pytest.raises(SymlinkViolationError):
        sync_workspace_to_worktree(workspace, worktree, ["backend/app/features/tasks/"])
    (workspace / "backend" / "app" / "features" / "tasks" / "evil").unlink()

    # Modifying file outside write set fails atomically without touching worktree
    (workspace / "README.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(WriteSetViolationError):
        sync_workspace_to_worktree(workspace, worktree, ["backend/app/features/tasks/"])
    assert (worktree / "README.md").read_text(encoding="utf-8") == "hello\n"
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")

    # Valid edit inside write set syncs back cleanly and keeps worktree .git intact
    router = workspace / "backend" / "app" / "features" / "tasks" / "router.py"
    router.write_text("router = 'ok'\n", encoding="utf-8")
    changed = sync_workspace_to_worktree(workspace, worktree, ["backend/app/features/tasks/"])
    assert changed == ["backend/app/features/tasks/router.py"]
    assert (worktree / "backend" / "app" / "features" / "tasks" / "router.py").exists()
    assert (worktree / ".git").exists()


@pytest.mark.asyncio
async def test_local_docker_provider_hardening_and_scoped_egress(tmp_path: Path) -> None:
    calls: dict[str, Any] = {"run": None, "execs": [], "connects": [], "disconnects": []}

    class _FakeContainer:
        id = "cid_123456"

        def exec_run(self, cmd: list[str], **kwargs: Any) -> Any:
            calls["execs"].append((cmd, kwargs))
            if cmd[:1] == ["fail_network"]:
                raise RuntimeError("boom during install")
            return SimpleNamespace(exit_code=0, output=(b"ok", b""))

        def remove(self, force: bool = False) -> None:
            calls["removed"] = force

    class _FakeNetwork:
        def __init__(self, name: str) -> None:
            self.name = name

        def connect(self, container: Any) -> None:
            calls["connects"].append((self.name, container.id))

        def disconnect(self, container: Any) -> None:
            calls["disconnects"].append((self.name, container.id))

    fake_container = _FakeContainer()

    def _run(**kwargs: Any) -> Any:
        calls["run"] = kwargs
        return fake_container

    fake_client = SimpleNamespace(
        containers=SimpleNamespace(run=_run, get=lambda cid: fake_container),
        networks=SimpleNamespace(get=lambda name: _FakeNetwork(name)),
    )

    ws = tmp_path / "ws"
    ws.mkdir()
    provider = LocalDockerProvider(
        RuntimeConfig(),
        HeavyExecSemaphore(2),
        client_factory=lambda: fake_client,
    )

    # Rejects if .git exists inside mounted workspace
    (ws / ".git").mkdir()
    with pytest.raises(SandboxError, match="\\.git-free"):
        await provider.create("t_1", "foundry-sandbox:sha256", ws, {})
    (ws / ".git").rmdir()

    sbx = await provider.create(
        "t_1",
        "foundry-sandbox:sha256",
        ws,
        {"DATABASE_URL": "postgresql+asyncpg://role:pw@foundry-postgres:5432/sbx_t1"},
    )
    run_kw = calls["run"]
    assert run_kw["cap_drop"] == ["ALL"]
    assert run_kw["security_opt"] == ["no-new-privileges:true"]
    assert run_kw["read_only"] is True
    assert run_kw["network"] == "foundry_sandbox_db"
    assert run_kw["dns"] == ["127.0.0.1"]
    assert run_kw["mem_limit"] == "1536m"
    assert run_kw["user"] == "1000:1000"

    # Offline exec has no egress attach and no proxy env
    res_off = await provider.exec(sbx, ["pytest", "-q"], network=False, heavy=True)
    assert res_off.exit_code == 0
    assert calls["connects"] == []
    assert calls["execs"][0][1]["environment"] is None

    # Network exec attaches egress net, injects proxy env, and always detaches even on error
    with pytest.raises(RuntimeError, match="boom"):
        await provider.exec(sbx, ["fail_network"], network=True, heavy=True)
    assert calls["connects"] == [("foundry_sandbox_egress", "cid_123456")]
    assert calls["disconnects"] == [("foundry_sandbox_egress", "cid_123456")]
    assert calls["execs"][1][1]["environment"]["HTTPS_PROXY"] == "http://foundry-egress-proxy:8888"

    await provider.destroy(sbx)
    assert calls["removed"] is True


@pytest.mark.asyncio
async def test_logical_db_manager_cloning_isolation_and_crash_safe_refresh() -> None:
    executed_sql: list[tuple[str, tuple[Any, ...]]] = []

    class _FakeConn:
        async def execute(self, sql: str, *args: Any) -> str:
            executed_sql.append((sql, args))
            return "OK"

        async def fetchval(self, sql: str, *args: Any) -> Any:
            executed_sql.append((sql, args))
            return 1

        async def close(self) -> None:
            executed_sql.append(("CLOSE", ()))

    async def _connect(_dsn: str) -> _FakeConn:
        return _FakeConn()

    with pytest.raises(LogicalDBError):
        sanitize_task_db_name("!!!")

    mgr = LogicalDBManager(
        "postgresql://foundry:pw@127.0.0.1:5433/foundry_blackboard",
        connect_fn=_connect,
    )
    sbx_db = await mgr.create_sandbox_db("t_01ABC")
    assert sbx_db.db_name == "sbx_t_01abc"
    assert sbx_db.role_name == "role_sbx_t_01abc"
    assert "foundry-postgres:5432/sbx_t_01abc" in sbx_db.database_url

    sql_text = "\n".join(s for s, _ in executed_sql)
    assert "pg_advisory_lock" in sql_text
    assert "pg_terminate_backend" in sql_text
    assert "CREATE DATABASE sbx_t_01abc TEMPLATE app_template" in sql_text
    assert "REVOKE CONNECT ON DATABASE sbx_t_01abc FROM PUBLIC" in sql_text
    assert "pg_advisory_unlock" in sql_text

    # Crash during refresh_app_template must STILL restore ALLOW_CONNECTIONS false + unlock
    executed_sql.clear()

    async def _failing_migrate(_dsn: str) -> None:
        raise RuntimeError("migration failed")

    with pytest.raises(RuntimeError, match="migration failed"):
        await mgr.refresh_app_template(_failing_migrate)

    refresh_sql = [s for s, _ in executed_sql]
    assert "ALTER DATABASE app_template WITH ALLOW_CONNECTIONS true" in refresh_sql
    assert "ALTER DATABASE app_template WITH ALLOW_CONNECTIONS false" in refresh_sql
    assert any("pg_advisory_unlock" in s for s in refresh_sql)
