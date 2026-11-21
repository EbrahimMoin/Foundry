"""Daemon-only hardened git wrapper with hook disabling and write-set enforcement."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

from foundry.security.workspace_guard import (
    ForbiddenGitMetaError,
    SymlinkViolationError,
    WriteSetViolationError,
    is_forbidden_git_meta,
    normalize_repo_path,
    path_in_write_set,
    validate_workspace_tree,
)


class GitError(Exception):
    """Raised when a hardened daemon-side git operation fails."""


def _parse_porcelain_z(raw_output: str) -> list[str]:
    """Extract all changed/untracked/renamed paths from `git status --porcelain -z -uall`."""
    if not raw_output:
        return []
    tokens = raw_output.split("\x00")
    paths: list[str] = []
    idx = 0
    while idx < len(tokens):
        entry = tokens[idx]
        idx += 1
        if not entry:
            continue
        status_code = entry[:2]
        path = entry[3:]
        paths.append(path)
        if ("R" in status_code or "C" in status_code) and idx < len(tokens):
            orig_path = tokens[idx]
            idx += 1
            if orig_path:
                paths.append(orig_path)
    return paths


class HardenedGit:
    """Runs git strictly daemon-side with hooks disabled and write-set/symlink enforcement."""

    def _clean_env(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
            env.pop(key, None)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_TERMINAL_PROMPT"] = "0"
        if os.name == "posix":
            env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        return env

    async def _run_git(self, cwd: Path, args: Sequence[str]) -> tuple[int, str, str]:
        resolved_cwd = await asyncio.to_thread(cwd.resolve)
        safe_dir = resolved_cwd.as_posix()
        cmd = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"safe.directory={safe_dir}",
            *args,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(resolved_cwd),
            env=self._clean_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        return (
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )

    async def _run_checked(self, cwd: Path, args: Sequence[str]) -> str:
        rc, out, err = await self._run_git(cwd, args)
        if rc != 0:
            raise GitError(f"git {' '.join(args)} failed (exit {rc}): {err.strip()}")
        return out

    async def init_repo(self, repo_dir: Path, *, initial_branch: str = "main") -> None:
        await asyncio.to_thread(repo_dir.mkdir, parents=True, exist_ok=True)
        await self._run_checked(repo_dir, ["init", "-b", initial_branch])

    async def create_worktree(
        self,
        repo_dir: Path,
        branch: str,
        base_ref: str,
        worktree_dir: Path,
    ) -> None:
        await asyncio.to_thread(worktree_dir.parent.mkdir, parents=True, exist_ok=True)
        await self._run_checked(
            repo_dir,
            ["worktree", "add", "-b", branch, str(worktree_dir), base_ref],
        )

    async def remove_worktree(self, repo_dir: Path, worktree_dir: Path) -> None:
        await self._run_checked(
            repo_dir,
            ["worktree", "remove", "--force", str(worktree_dir)],
        )

    async def status_paths(self, worktree_dir: Path) -> list[str]:
        await asyncio.to_thread(validate_workspace_tree, worktree_dir, ignore_git_dir=True)
        out = await self._run_checked(worktree_dir, ["status", "--porcelain", "-z", "-uall"])
        return [normalize_repo_path(p) for p in _parse_porcelain_z(out)]

    async def enforce_write_set(
        self,
        worktree_dir: Path,
        write_set: Sequence[str],
        *,
        allow_infra: bool = False,
    ) -> list[str]:
        changed = await self.status_paths(worktree_dir)
        for path in changed:
            if is_forbidden_git_meta(path, allow_infra=allow_infra):
                raise ForbiddenGitMetaError(f"Forbidden git metadata change: {path}")
        offending = [p for p in changed if not path_in_write_set(p, write_set)]
        if offending:
            raise WriteSetViolationError(offending, write_set)
        return changed

    async def assert_no_git_symlinks(self, worktree_dir: Path) -> None:
        out = await self._run_checked(worktree_dir, ["ls-files", "--stage"])
        for line in out.splitlines():
            if line.startswith("120000 "):
                path_part = line.split("\t", 1)[-1]
                raise SymlinkViolationError(f"Git mode 120000 symlink rejected: {path_part}")

    async def commit(
        self,
        worktree_dir: Path,
        message: str,
        write_set: Sequence[str],
        *,
        author_name: str = "Foundry Agent",
        author_email: str = "agent@foundry.local",
        allow_infra: bool = False,
    ) -> str:
        changed = await self.enforce_write_set(worktree_dir, write_set, allow_infra=allow_infra)
        if not changed:
            raise GitError("Nothing to commit in worktree")

        await self._run_checked(worktree_dir, ["add", "-A"])
        try:
            await self.assert_no_git_symlinks(worktree_dir)
        except SymlinkViolationError:
            await self._run_git(worktree_dir, ["reset"])
            raise

        await self._run_checked(
            worktree_dir,
            [
                "-c",
                f"user.name={author_name}",
                "-c",
                f"user.email={author_email}",
                "commit",
                "-m",
                message,
            ],
        )
        sha = await self._run_checked(worktree_dir, ["rev-parse", "HEAD"])
        return sha.strip()

    async def diff(self, worktree_dir: Path, base_ref: str | None = None) -> str:
        await asyncio.to_thread(validate_workspace_tree, worktree_dir, ignore_git_dir=True)
        args = ["diff", f"{base_ref}...HEAD"] if base_ref else ["diff"]
        return await self._run_checked(worktree_dir, args)

    async def diff_names(self, worktree_dir: Path, base_ref: str) -> list[str]:
        await asyncio.to_thread(validate_workspace_tree, worktree_dir, ignore_git_dir=True)
        out = await self._run_checked(worktree_dir, ["diff", "--name-only", f"{base_ref}...HEAD"])
        return [normalize_repo_path(line.strip()) for line in out.splitlines() if line.strip()]

    async def log(self, worktree_dir: Path, limit: int = 20) -> list[dict[str, str]]:
        out = await self._run_checked(
            worktree_dir,
            ["log", f"-n{limit}", "--format=%H%x09%an%x09%s"],
        )
        entries: list[dict[str, str]] = []
        for line in out.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                entries.append({"sha": parts[0], "author": parts[1], "subject": parts[2]})
        return entries
