"""Unit tests for WorkspaceGuard and daemon-side HardenedGit security invariants."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
from pathlib import Path

import pytest

from foundry.security import (
    ForbiddenGitMetaError,
    HardenedGit,
    PathTraversalError,
    SpecialFileViolationError,
    SymlinkViolationError,
    WriteSetViolationError,
    YamlBombError,
    is_forbidden_git_meta,
    normalize_repo_path,
    path_in_write_set,
    paths_overlap,
    validate_workspace_tree,
)


def test_path_normalization_prefix_overlap_and_git_meta() -> None:
    assert normalize_repo_path("./backend\\app/features/tasks/router.py") == (
        "backend/app/features/tasks/router.py"
    )
    assert normalize_repo_path("backend/app/features/tasks/", allow_directory_slash=True) == (
        "backend/app/features/tasks/"
    )

    with pytest.raises(PathTraversalError):
        normalize_repo_path("../secrets.txt")
    with pytest.raises(PathTraversalError):
        normalize_repo_path("/etc/passwd")
    with pytest.raises(PathTraversalError):
        normalize_repo_path("C:\\Windows\\System32")

    assert is_forbidden_git_meta(".git/config") is True
    assert is_forbidden_git_meta(".gitattributes", allow_infra=False) is True
    assert is_forbidden_git_meta(".gitattributes", allow_infra=True) is False
    assert is_forbidden_git_meta(".github/workflows/ci.yml", allow_infra=False) is False

    ws = ["backend/app/features/tasks/", "pyproject.toml"]
    assert path_in_write_set("backend/app/features/tasks/router.py", ws) is True
    assert path_in_write_set("backend/app/features/auth/router.py", ws) is False
    assert path_in_write_set("pyproject.toml", ws) is True

    conflicts = paths_overlap(
        ["backend/app/features/tasks/"],
        ["backend/app/features/tasks/service.py", "frontend/src/features/tasks/"],
    )
    assert conflicts == [("backend/app/features/tasks/", "backend/app/features/tasks/service.py")]


def test_validate_workspace_tree_blocks_symlinks_fifos_and_yaml_bombs(tmp_path: Path) -> None:
    good = tmp_path / "good"
    good.mkdir()
    (good / "openapi.yaml").write_text("openapi: 3.1.0\npaths: {}\n", encoding="utf-8")
    validate_workspace_tree(good)

    # Symlink rejection
    sym_dir = tmp_path / "sym_dir"
    sym_dir.mkdir()
    os.symlink("/etc/passwd", sym_dir / "leak.txt")
    with pytest.raises(SymlinkViolationError):
        validate_workspace_tree(sym_dir)

    # FIFO special file rejection (POSIX)
    if hasattr(os, "mkfifo"):
        fifo_dir = tmp_path / "fifo_dir"
        fifo_dir.mkdir()
        os.mkfifo(fifo_dir / "pipe")
        with pytest.raises(SpecialFileViolationError):
            validate_workspace_tree(fifo_dir)

    # Oversized YAML rejection
    big_dir = tmp_path / "big_yaml"
    big_dir.mkdir()
    (big_dir / "huge.yaml").write_text("a: 1\n" * 500, encoding="utf-8")
    with pytest.raises(YamlBombError, match="exceeds size limit"):
        validate_workspace_tree(big_dir, max_yaml_bytes=128)

    # YAML alias expansion bomb rejection
    bomb_dir = tmp_path / "bomb_yaml"
    bomb_dir.mkdir()
    (bomb_dir / "bomb.yml").write_text(
        "a: &a ['lol']\nb: [*a, *a, *a, *a, *a, *a]\n",
        encoding="utf-8",
    )
    with pytest.raises(YamlBombError, match="alias limit"):
        validate_workspace_tree(bomb_dir, max_yaml_aliases=4)


@pytest.mark.asyncio
async def test_hardened_git_disables_hooks_blocks_oob_edits_and_rejects_mode_120000(
    tmp_path: Path,
) -> None:
    git = HardenedGit()
    repo = tmp_path / "repo"
    await git.init_repo(repo)
    (repo / "README.md").write_text("# App\n", encoding="utf-8")
    initial_sha = await git.commit(repo, "init", ["README.md"], allow_infra=True)
    assert len(initial_sha) == 40

    # Plant a malicious pre-commit hook that creates a sentinel file if executed
    sentinel = tmp_path / "hook_executed.txt"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho PWNED > {sentinel}\nexit 1\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    worktree = tmp_path / "wt_task1"
    await git.create_worktree(repo, "task/t1-bookmarks", "main", worktree)

    # Write inside reserved write set and commit -> hook MUST NOT execute!
    target = worktree / "backend" / "app" / "features" / "bookmarks" / "router.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("router = None\n", encoding="utf-8")
    sha = await git.commit(
        worktree,
        "feat: bookmarks router",
        ["backend/app/features/bookmarks/"],
    )
    assert len(sha) == 40
    assert not sentinel.exists()
    assert await git.diff_names(worktree, initial_sha) == [
        "backend/app/features/bookmarks/router.py"
    ]
    assert (await git.log(worktree, limit=1))[0]["sha"] == sha

    # Out-of-write-set file rejected
    (worktree / "backend" / "app" / "core_hack.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(WriteSetViolationError):
        await git.commit(worktree, "oob", ["backend/app/features/bookmarks/"])
    (worktree / "backend" / "app" / "core_hack.py").unlink()

    # Forbidden .gitattributes edit rejected for non-infra task
    (worktree / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
    with pytest.raises(ForbiddenGitMetaError):
        await git.commit(
            worktree,
            "gitmeta",
            ["backend/app/features/bookmarks/", ".gitattributes"],
            allow_infra=False,
        )
    (worktree / ".gitattributes").unlink()

    # Git index mode 120000 (symlink) entry rejected
    git_bin = shutil.which("git")
    assert git_bin is not None
    hash_proc = await asyncio.create_subprocess_exec(
        git_bin,
        "hash-object",
        "-w",
        "--stdin",
        cwd=str(worktree),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout_b, _ = await hash_proc.communicate(b"/etc/passwd")
    assert hash_proc.returncode == 0
    blob = stdout_b.decode("utf-8").strip()

    idx_proc = await asyncio.create_subprocess_exec(
        git_bin,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{blob},backend/app/features/bookmarks/sym",
        cwd=str(worktree),
    )
    assert await idx_proc.wait() == 0
    with pytest.raises(SymlinkViolationError, match="120000"):
        await git.assert_no_git_symlinks(worktree)
