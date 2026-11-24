"""SandboxProvider Protocol, priority levels, and package install policy models."""

from __future__ import annotations

import re
from enum import IntEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

_PKG_VERSION_SPLIT_RE = re.compile(r"[=<>!~@;\s\[]")


class ExecPriority(IntEnum):
    """Execution priority for the 2-slot HeavyExecSemaphore (lower value = higher priority)."""

    MERGE_QUEUE_G5 = 0
    VERIFY_G3 = 1
    IMPLEMENT_G1_G2 = 2


class Sandbox(BaseModel):
    """Represents a running .git-free sandbox container and its workspace."""

    task_id: str
    container_id: str
    workspace_dir: Path
    image: str
    db_name: str | None = None
    database_url: str | None = None
    ephemeral_volumes: list[str] = Field(default_factory=list)


class ExecResult(BaseModel):
    """Result of executing a command inside a sandbox."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False


class PackageInstallPolicy(BaseModel):
    """Governs unplanned package installs (curated allowlist OR hard human approval)."""

    curated_python_allowlist: frozenset[str] = Field(default_factory=frozenset)
    curated_npm_allowlist: frozenset[str] = Field(default_factory=frozenset)
    denylist: frozenset[str] = Field(default_factory=frozenset)
    human_approved_packages: set[str] = Field(default_factory=set)

    @staticmethod
    def normalize_pkg_name(raw_spec: str) -> str:
        """Extract normalized base package name (preserving npm @scope/name)."""
        spec = raw_spec.strip()
        if spec.startswith("@") and "/" in spec:
            scope, rest = spec.split("/", 1)
            base = _PKG_VERSION_SPLIT_RE.split(rest, maxsplit=1)[0]
            return f"{scope}/{base}".lower()
        return _PKG_VERSION_SPLIT_RE.split(spec, maxsplit=1)[0].lower()

    def is_allowed(self, ecosystem: str, package_spec: str) -> bool:
        norm = self.normalize_pkg_name(package_spec)
        if not norm or norm in self.denylist:
            return False
        if norm in self.human_approved_packages:
            return True
        if ecosystem == "python":
            return norm in self.curated_python_allowlist
        if ecosystem == "npm":
            return norm in self.curated_npm_allowlist
        return False


@runtime_checkable
class SandboxProvider(Protocol):
    """Pluggable sandbox backend (v1: LocalDockerProvider)."""

    async def create(
        self,
        task_id: str,
        image: str,
        workspace: Path,
        env: dict[str, str],
    ) -> Sandbox: ...

    async def exec(
        self,
        sandbox: Sandbox,
        cmd: list[str],
        *,
        network: bool = False,
        heavy: bool = False,
        priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
        timeout_s: int = 600,
    ) -> ExecResult: ...

    async def destroy(self, sandbox: Sandbox) -> None: ...
