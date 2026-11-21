"""Daemon-side sandbox-mcp server enforcing path jail, command allowlist, and package gate."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer

from foundry.sandbox.protocol import (
    ExecPriority,
    ExecResult,
    PackageInstallPolicy,
    Sandbox,
    SandboxProvider,
)
from foundry.security.workspace_guard import (
    ForbiddenGitMetaError,
    SymlinkViolationError,
    WriteSetViolationError,
    is_forbidden_git_meta,
    normalize_repo_path,
    path_in_write_set,
)

_ALLOWED_COMMANDS = frozenset(
    {
        "alembic",
        "cat",
        "eslint",
        "find",
        "ls",
        "mypy",
        "node",
        "npx",
        "pnpm",
        "pytest",
        "python",
        "python3",
        "rg",
        "ruff",
        "schemathesis",
        "tsc",
        "uv",
        "vitest",
    }
)
_HEAVY_COMMANDS = frozenset({"pytest", "mypy", "tsc", "vitest", "schemathesis"})
_SAFE_PKG_SPEC_RE = re.compile(r"^(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9._\[\]-]+(?:[<>=!~@^].+)?$")


class CommandNotAllowedError(Exception):
    """Raised when an agent attempts a blocked binary, subcommand, or out-of-scope --fix."""


class PackageApprovalRequiredError(Exception):
    """Raised when an unplanned package is not curated and lacks hard human Approve (A7-10)."""


class SandboxMCPServer:
    """Enforces path jail, command allowlist, sequential heavy tests, and package gating."""

    def __init__(
        self,
        provider: SandboxProvider,
        sandbox: Sandbox,
        *,
        write_set: Sequence[str],
        package_policy: PackageInstallPolicy,
        allow_infra: bool = False,
    ) -> None:
        self._provider = provider
        self._sandbox = sandbox
        self._write_set = [normalize_repo_path(p, allow_directory_slash=True) for p in write_set]
        self._package_policy = package_policy
        self._allow_infra = allow_infra
        self.pending_deps: list[dict[str, str]] = []

    def _resolve_jailed_path(self, rel_path: str) -> tuple[str, Path]:
        norm = normalize_repo_path(rel_path)
        root = self._sandbox.workspace_dir.resolve()
        candidate = (root / norm).resolve(strict=False)
        if root not in candidate.parents and candidate != root:
            raise SymlinkViolationError(f"Resolved path escapes workspace root: {rel_path}")

        # Walk ancestor segments inside workspace to ensure no symlink traversal exists
        cur = root
        for part in Path(norm).parts:
            cur = cur / part
            if cur.is_symlink():
                raise SymlinkViolationError(f"Symlink prohibited inside workspace: {cur}")
        return norm, root / norm

    def read_file(self, path: str) -> str:
        """Read a UTF-8 file inside the sandbox workspace after symlink & jail checks."""
        _, target = self._resolve_jailed_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found in workspace: {path}")
        return target.read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> str:
        """Write a file strictly inside the task's reserved write set and path jail."""
        norm, target = self._resolve_jailed_path(path)
        if is_forbidden_git_meta(norm, allow_infra=self._allow_infra):
            raise ForbiddenGitMetaError(f"Writing git metadata is forbidden: {norm}")
        if not path_in_write_set(norm, self._write_set):
            raise WriteSetViolationError([norm], self._write_set)
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.path.islink(target):
            raise SymlinkViolationError(f"Cannot overwrite symlink target: {norm}")
        target.write_text(content, encoding="utf-8")
        return norm

    def _validate_command(self, cmd: list[str]) -> tuple[list[str], bool]:
        if not cmd:
            raise CommandNotAllowedError("Empty command is not allowed")
        base_bin = Path(cmd[0]).name
        if base_bin not in _ALLOWED_COMMANDS:
            raise CommandNotAllowedError(f"Command '{base_bin}' is not on the sandbox allowlist")

        # Package installs must go through install_package() so allowlist/proxy rules apply
        if base_bin == "uv" and len(cmd) >= 3 and cmd[1] == "pip" and cmd[2] == "install":
            raise CommandNotAllowedError("Use install_package() instead of 'uv pip install'")
        if base_bin == "pnpm" and any(tok in {"add", "install", "i"} for tok in cmd[1:3]):
            raise CommandNotAllowedError("Use install_package() instead of 'pnpm add/install'")

        sanitized = list(cmd)
        if "--fix" in sanitized or "--write" in sanitized:
            ignored_subcmds = {"check", "format"} if base_bin == "ruff" else set()
            path_args = [
                arg
                for arg in sanitized[1:]
                if not arg.startswith("-") and arg not in ignored_subcmds
            ]
            if not path_args:
                sanitized.extend(self._write_set)
            else:
                for raw_arg in path_args:
                    norm_arg = normalize_repo_path(raw_arg, allow_directory_slash=True)
                    if is_forbidden_git_meta(norm_arg, allow_infra=self._allow_infra):
                        raise ForbiddenGitMetaError(f"Formatter cannot target git meta: {norm_arg}")
                    if not path_in_write_set(norm_arg, self._write_set):
                        raise WriteSetViolationError([norm_arg], self._write_set)

        is_heavy = base_bin in _HEAVY_COMMANDS or (
            base_bin in {"pnpm", "npx"} and any(t in {"tsc", "vitest", "test"} for t in sanitized)
        )
        return sanitized, is_heavy

    async def run_command(
        self,
        cmd: list[str],
        *,
        heavy: bool = False,
        priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
        timeout_s: int = 300,
    ) -> ExecResult:
        """Execute an allowlisted, non-network command inside the sandbox."""
        sanitized_cmd, inferred_heavy = self._validate_command(cmd)
        return await self._provider.exec(
            self._sandbox,
            sanitized_cmd,
            network=False,
            heavy=heavy or inferred_heavy,
            priority=priority,
            timeout_s=timeout_s,
        )

    async def run_tests(
        self,
        commands: Sequence[list[str]],
        *,
        priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
    ) -> list[ExecResult]:
        """Run verification commands sequentially under the 2-slot heavy-exec semaphore."""
        results: list[ExecResult] = []
        for cmd in commands:
            sanitized_cmd, _ = self._validate_command(list(cmd))
            res = await self._provider.exec(
                self._sandbox,
                sanitized_cmd,
                network=False,
                heavy=True,
                priority=priority,
            )
            results.append(res)
            if res.exit_code != 0:
                break
        return results

    async def install_package(
        self,
        ecosystem: Literal["python", "npm"],
        package: str,
        *,
        priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
    ) -> ExecResult:
        """Install an allowlisted or human-approved package with scripts disabled (A7-10)."""
        spec = package.strip()
        if not spec or spec.startswith("-") or "://" in spec or "git+" in spec:
            raise CommandNotAllowedError(f"Unsafe package specification rejected: {package!r}")
        if not _SAFE_PKG_SPEC_RE.match(spec):
            raise CommandNotAllowedError(f"Malformed package specification: {package!r}")

        norm_name = self._package_policy.normalize_pkg_name(spec)
        if norm_name in self._package_policy.denylist:
            raise CommandNotAllowedError(f"Package '{norm_name}' is on the security denylist")

        if not self._package_policy.is_allowed(ecosystem, spec):
            raise PackageApprovalRequiredError(
                f"Package '{norm_name}' ({ecosystem}) is not in curated allowlist; "
                "hard human Approve decision is required before installation."
            )

        if ecosystem == "python":
            cmd = ["uv", "pip", "install", "--only-binary", ":all:", spec]
        else:
            cmd = ["pnpm", "add", "--ignore-scripts", spec]

        result = await self._provider.exec(
            self._sandbox,
            cmd,
            network=True,
            heavy=True,
            priority=priority,
        )
        if result.exit_code == 0:
            self.pending_deps.append({"ecosystem": ecosystem, "package": spec})
        return result

    def build_mcp_server(self) -> MCPServer:
        """Build an official MCPServer (mcp v2) exposing sandbox-mcp tools."""
        server = MCPServer("foundry-sandbox-mcp")

        @server.tool(description="Read a file inside the sandbox /workspace jail.")
        def read_file(path: str) -> str:
            return self.read_file(path)

        @server.tool(description="Write a file strictly inside the task's reserved write set.")
        def write_file(path: str, content: str) -> str:
            return self.write_file(path, content)

        @server.tool(description="Run an allowlisted command inside the offline sandbox.")
        async def run_command(cmd: list[str], heavy: bool = False) -> dict[str, object]:
            res = await self.run_command(cmd, heavy=heavy)
            return res.model_dump()

        @server.tool(
            description="Run sequential test/lint commands under the heavy-exec semaphore."
        )
        async def run_tests(commands: list[list[str]]) -> list[dict[str, object]]:
            results = await self.run_tests(commands)
            return [r.model_dump() for r in results]

        @server.tool(
            description="Install a curated or human-approved package through the egress proxy."
        )
        async def install_package(
            ecosystem: Literal["python", "npm"],
            package: str,
        ) -> dict[str, object]:
            res = await self.install_package(ecosystem=ecosystem, package=package)
            return res.model_dump()

        return server

    build_fastmcp = build_mcp_server
