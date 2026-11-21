"""Unit tests for FastAPIReactPostgresPack and daemon git-mcp / sandbox-mcp servers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from foundry.mcp_servers import (
    CommandNotAllowedError,
    GitMCPServer,
    PackageApprovalRequiredError,
    SandboxMCPServer,
)
from foundry.sandbox import (
    ExecPriority,
    ExecResult,
    Sandbox,
    sync_worktree_to_workspace,
)
from foundry.security import (
    HardenedGit,
    PathTraversalError,
    SymlinkViolationError,
    WriteSetViolationError,
)
from foundry.stack_packs import FastAPIReactPostgresPack, get_stack_pack


class _StubSandboxProvider:
    def __init__(self) -> None:
        self.exec_calls: list[tuple[list[str], bool, bool, ExecPriority]] = []

    async def create(
        self, task_id: str, image: str, workspace: Path, env: dict[str, str]
    ) -> Sandbox:
        return Sandbox(
            task_id=task_id, container_id="cid_stub", workspace_dir=workspace, image=image
        )

    async def exec(
        self,
        sandbox: Sandbox,
        cmd: list[str],
        *,
        network: bool = False,
        heavy: bool = False,
        priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
        timeout_s: int = 600,
    ) -> ExecResult:
        self.exec_calls.append((cmd, network, heavy, priority))
        return ExecResult(exit_code=0, stdout="ok")

    async def destroy(self, sandbox: Sandbox) -> None:
        return None


def test_fastapi_react_postgres_scaffold_and_conventions(tmp_path: Path) -> None:
    pack = get_stack_pack("fastapi-react-postgres")
    assert isinstance(pack, FastAPIReactPostgresPack)
    assert pack.default_write_set("backend", "bookmarks") == ["backend/app/features/bookmarks/"]
    assert pack.default_write_set("frontend", "bookmarks") == ["frontend/src/features/bookmarks/"]
    assert pack.default_write_set("backend", "bookmarks", kind="integration") == [
        "backend/app/features/bookmarks/",
        "frontend/src/features/bookmarks/",
    ]
    assert pack.default_write_set("data", kind="schema") == [
        "backend/app/models/",
        "backend/migrations/",
    ]

    app_dir = tmp_path / "bookmark-app"
    written = pack.scaffold(app_dir, app_name="Bookmark App")
    assert len(written) >= 25
    assert (app_dir / "backend" / "app" / "main.py").exists()
    assert "pkgutil.iter_modules" in (app_dir / "backend" / "app" / "main.py").read_text(
        encoding="utf-8"
    )
    conftest_txt = (app_dir / "backend" / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "seed_user" in conftest_txt
    assert "RequestValidationRecorder" in conftest_txt
    assert "import.meta.glob" in (app_dir / "frontend" / "src" / "app" / "AppLayout.tsx").read_text(
        encoding="utf-8"
    )
    assert (app_dir / "contracts" / "openapi.yaml").exists()
    assert (app_dir / ".spectral.yaml").exists()
    assert (app_dir / "infra" / "compose.local.yml").exists()


@pytest.mark.asyncio
async def test_sandbox_mcp_and_git_mcp_enforce_guardrails_and_register_tools(
    tmp_path: Path,
) -> None:
    pack = FastAPIReactPostgresPack()
    git = HardenedGit()
    repo = tmp_path / "repo"
    await git.init_repo(repo)
    pack.scaffold(repo, app_name="TaskFlow")
    base_sha = await git.commit(
        repo,
        "chore: wave0 scaffold",
        ["backend/", "frontend/", "contracts/", ".spectral.yaml", "e2e/", "infra/", ".github/"],
        allow_infra=True,
    )

    worktree = tmp_path / "wt_tasks"
    await git.create_worktree(repo, "task/t1-tasks", "main", worktree)
    workspace = tmp_path / "ws_tasks"
    sync_worktree_to_workspace(worktree, workspace)

    provider = _StubSandboxProvider()
    sbx = Sandbox(
        task_id="t1",
        container_id="cid_stub",
        workspace_dir=workspace,
        image=pack.sandbox_image,
    )
    write_set = pack.default_write_set("backend", "tasks")
    policy = pack.curated_package_policy()

    sandbox_mcp = SandboxMCPServer(
        provider,
        sbx,
        write_set=write_set,
        package_policy=policy,
    )
    git_mcp = GitMCPServer(
        git,
        worktree_dir=worktree,
        workspace_dir=workspace,
        write_set=write_set,
    )

    # Path jail + write_set enforcement
    with pytest.raises(PathTraversalError):
        sandbox_mcp.read_file("../../etc/passwd")
    with pytest.raises(WriteSetViolationError):
        sandbox_mcp.write_file("backend/app/main.py", "# tampered")

    # Symlink read rejection inside workspace
    os.symlink("/etc/passwd", workspace / "sym_leak")
    with pytest.raises(SymlinkViolationError):
        sandbox_mcp.read_file("sym_leak")
    (workspace / "sym_leak").unlink()

    # Valid write + read
    written_rel = sandbox_mcp.write_file(
        "backend/app/features/tasks/router.py",
        "from fastapi import APIRouter\nrouter = APIRouter(prefix='/tasks')\n",
    )
    assert written_rel == "backend/app/features/tasks/router.py"
    assert "APIRouter" in sandbox_mcp.read_file("backend/app/features/tasks/router.py")

    # Command allowlist + scoped --fix + heavy auto-marking
    with pytest.raises(CommandNotAllowedError):
        await sandbox_mcp.run_command(["git", "status"])
    with pytest.raises(CommandNotAllowedError):
        await sandbox_mcp.run_command(["curl", "https://example.com"])
    with pytest.raises(WriteSetViolationError):
        await sandbox_mcp.run_command(["ruff", "check", "--fix", "backend/app/core/config.py"])

    await sandbox_mcp.run_command(["ruff", "check", "--fix"])
    assert provider.exec_calls[-1][0] == ["ruff", "check", "--fix", "backend/app/features/tasks/"]
    assert provider.exec_calls[-1][1] is False  # network=False

    await sandbox_mcp.run_tests([["pytest", "-q", "backend/tests"]])
    assert provider.exec_calls[-1][2] is True  # heavy=True

    # Curated vs unapproved package install enforcement (A7-10)
    res_curated = await sandbox_mcp.install_package("python", "python-slugify>=8.0")
    assert res_curated.exit_code == 0
    assert provider.exec_calls[-1][0] == [
        "uv",
        "pip",
        "install",
        "--only-binary",
        ":all:",
        "python-slugify>=8.0",
    ]
    assert provider.exec_calls[-1][1] is True  # network=True
    assert sandbox_mcp.pending_deps == [{"ecosystem": "python", "package": "python-slugify>=8.0"}]

    with pytest.raises(PackageApprovalRequiredError):
        await sandbox_mcp.install_package("npm", "some-unknown-pkg")

    policy.human_approved_packages.add("some-unknown-pkg")
    res_approved = await sandbox_mcp.install_package("npm", "some-unknown-pkg")
    assert res_approved.exit_code == 0
    assert provider.exec_calls[-1][0] == ["pnpm", "add", "--ignore-scripts", "some-unknown-pkg"]

    # Daemon git-mcp commit enforces sync + write set
    commit_sha = await git_mcp.commit("feat(tasks): add tasks router")
    assert len(commit_sha) == 40
    assert await git.diff_names(worktree, base_sha) == ["backend/app/features/tasks/router.py"]

    # Official mcp v2 MCPServer instances register tools cleanly
    sbx_mcp_app = sandbox_mcp.build_mcp_server()
    git_mcp_app = git_mcp.build_mcp_server()
    assert {t.name for t in await sbx_mcp_app.list_tools()} == {
        "read_file",
        "write_file",
        "run_command",
        "run_tests",
        "install_package",
    }
    assert {t.name for t in await git_mcp_app.list_tools()} == {"status", "diff", "log", "commit"}
