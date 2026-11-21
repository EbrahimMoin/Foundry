"""Daemon-side git-mcp server with symlink/mode-120000 and write-set enforcement."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from foundry.sandbox.workspace_sync import sync_workspace_to_worktree
from foundry.security.hardened_git import HardenedGit


class GitMCPServer:
    """Exposes hardened git tools strictly on the daemon host (agents never run git)."""

    def __init__(
        self,
        git: HardenedGit,
        *,
        worktree_dir: Path,
        workspace_dir: Path,
        write_set: Sequence[str],
        allow_infra: bool = False,
    ) -> None:
        self._git = git
        self._worktree_dir = worktree_dir
        self._workspace_dir = workspace_dir
        self._write_set = list(write_set)
        self._allow_infra = allow_infra

    async def status(self) -> list[str]:
        """Sync-validate workspace edits into the worktree and return changed repo paths."""
        sync_workspace_to_worktree(
            self._workspace_dir,
            self._worktree_dir,
            self._write_set,
            allow_infra=self._allow_infra,
        )
        return await self._git.status_paths(self._worktree_dir)

    async def diff(self, base_ref: str | None = None) -> str:
        """Return unified diff for the task worktree after symlink pre-walk."""
        return await self._git.diff(self._worktree_dir, base_ref=base_ref)

    async def log(self, limit: int = 20) -> list[dict[str, str]]:
        """Return recent commit entries for the task branch."""
        return await self._git.log(self._worktree_dir, limit=limit)

    async def commit(self, message: str) -> str:
        """Validate symlinks + write set, sync workspace -> worktree, and commit on daemon."""
        sync_workspace_to_worktree(
            self._workspace_dir,
            self._worktree_dir,
            self._write_set,
            allow_infra=self._allow_infra,
        )
        return await self._git.commit(
            self._worktree_dir,
            message,
            self._write_set,
            allow_infra=self._allow_infra,
        )

    def build_mcp_server(self) -> MCPServer:
        """Build an official MCPServer (mcp v2) exposing git-mcp tools."""
        server = MCPServer("foundry-git-mcp")

        @server.tool(description="Validate workspace changes and list modified repository paths.")
        async def status() -> list[str]:
            return await self.status()

        @server.tool(description="Return unified git diff for the task worktree.")
        async def diff(base_ref: str | None = None) -> str:
            return await self.diff(base_ref=base_ref)

        @server.tool(description="List recent commits on the task branch.")
        async def log(limit: int = 20) -> list[dict[str, str]]:
            return await self.log(limit=limit)

        @server.tool(description="Commit write-set-validated workspace edits daemon-side.")
        async def commit(message: str) -> str:
            return await self.commit(message)

        return server

    build_fastmcp = build_mcp_server
