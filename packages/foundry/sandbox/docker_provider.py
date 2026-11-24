"""LocalDockerProvider: hardened .git-free container runner with scoped egress proxy."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import docker

from foundry.config.schemas import RuntimeConfig
from foundry.sandbox.protocol import ExecPriority, ExecResult, Sandbox
from foundry.sandbox.semaphore import HeavyExecSemaphore
from foundry.security.scanner import SecretLeakError, SecretScanner
from foundry.security.workspace_guard import validate_workspace_tree


class SandboxError(Exception):
    """Raised when sandbox creation, isolation checks, or execution fails."""


class LocalDockerProvider:
    """Implements SandboxProvider over native Docker Engine with strict hardening."""

    def __init__(
        self,
        runtime_cfg: RuntimeConfig,
        semaphore: HeavyExecSemaphore,
        *,
        client_factory: Callable[[], Any] | None = None,
        scanner: SecretScanner | None = None,
        sandbox_db_network: str = "foundry_sandbox_db",
        egress_network: str = "foundry_sandbox_egress",
        proxy_url: str = "http://foundry-egress-proxy:8888",
        mount_dep_volumes: bool = True,
    ) -> None:
        self._runtime_cfg = runtime_cfg
        self._semaphore = semaphore
        self._client_factory = client_factory or docker.from_env
        self._scanner = scanner or SecretScanner()
        self._sandbox_db_network = sandbox_db_network
        self._egress_network = egress_network
        self._proxy_url = proxy_url
        self._mount_dep_volumes = mount_dep_volumes
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    @staticmethod
    def _validate_mount_workspace(workspace: Path) -> None:
        git_marker = workspace / ".git"
        if git_marker.exists() or git_marker.is_symlink():
            raise SandboxError(
                f"Container workspace must be .git-free (found {git_marker}); "
                "use sync_worktree_to_workspace first"
            )
        validate_workspace_tree(workspace, ignore_git_dir=False)

    async def create(
        self,
        task_id: str,
        image: str,
        workspace: Path,
        env: dict[str, str],
    ) -> Sandbox:
        await asyncio.to_thread(self._validate_mount_workspace, workspace)
        resolved_ws = await asyncio.to_thread(workspace.resolve)

        for key, value in env.items():
            matches = self._scanner.scan_text(value)
            if matches:
                raise SecretLeakError(matches)
            if "API_KEY" in key.upper() or "TOKEN" in key.upper():
                raise SandboxError(f"Secrets cannot enter sandbox environment: {key}")

        limits = self._runtime_cfg.sandbox_limits
        merged_env = {
            "VIRTUAL_ENV": "/opt/venv",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "/workspace/backend",
            "MYPYPATH": "/workspace/backend",
            "RUFF_CACHE_DIR": "/tmp/.ruff_cache",  # noqa: S108
            "MYPY_CACHE_DIR": "/tmp/.mypy_cache",  # noqa: S108
            "PYTHONPYCACHEPREFIX": "/tmp/__pycache__",  # noqa: S108
            "XDG_CACHE_HOME": "/tmp/.cache",  # noqa: S108
            **env,
            "NODE_OPTIONS": f"--max-old-space-size={limits.node_max_old_space_mb}",
        }
        container_name = f"foundry-sbx-{task_id}"
        container_tmpfs = {"/tmp": "rw,noexec,nosuid,size=256m"}  # noqa: S108
        volumes: dict[str, dict[str, str]] = {
            str(resolved_ws): {"bind": "/workspace", "mode": "rw"}
        }
        ephemeral_volumes: list[str] = []
        if self._mount_dep_volumes:
            safe_vol_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in task_id)
            venv_vol = f"foundry-venv-{safe_vol_id}"
            volumes[venv_vol] = {"bind": "/opt/venv", "mode": "rw"}
            ephemeral_volumes.append(venv_vol)
            if (resolved_ws / "frontend").is_dir():
                await asyncio.to_thread(
                    (resolved_ws / "frontend" / "node_modules").mkdir,
                    parents=True,
                    exist_ok=True,
                )
                nm_vol = f"foundry-nm-{safe_vol_id}"
                volumes[nm_vol] = {"bind": "/workspace/frontend/node_modules", "mode": "rw"}
                ephemeral_volumes.append(nm_vol)

        run_kwargs: dict[str, Any] = {
            "image": image,
            "name": container_name,
            "command": ["sleep", "infinity"],
            "detach": True,
            "working_dir": "/workspace",
            "user": "1000:1000",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only": True,
            "tmpfs": container_tmpfs,
            "mem_limit": limits.memory,
            "nano_cpus": int(limits.cpus * 1_000_000_000),
            "pids_limit": limits.pids,
            "network": self._sandbox_db_network,
            "dns": ["127.0.0.1"],
            "volumes": volumes,
            "environment": merged_env,
        }

        client = self._get_client()
        container = await asyncio.to_thread(client.containers.run, **run_kwargs)
        return Sandbox(
            task_id=task_id,
            container_id=str(container.id),
            workspace_dir=resolved_ws,
            image=image,
            database_url=env.get("DATABASE_URL"),
            ephemeral_volumes=ephemeral_volumes,
        )

    async def _run_exec_sync(
        self,
        sandbox: Sandbox,
        cmd: list[str],
        *,
        network: bool,
        timeout_s: int,
    ) -> ExecResult:
        client = self._get_client()
        container = await asyncio.to_thread(client.containers.get, sandbox.container_id)
        exec_env: dict[str, str] | None = None
        egress_net: Any | None = None

        start = time.perf_counter()
        try:
            if network:
                connected_net = await asyncio.to_thread(client.networks.get, self._egress_network)
                await asyncio.to_thread(connected_net.connect, container)
                egress_net = connected_net
                exec_env = {
                    "HTTP_PROXY": self._proxy_url,
                    "HTTPS_PROXY": self._proxy_url,
                    "http_proxy": self._proxy_url,
                    "https_proxy": self._proxy_url,
                }

            try:
                raw_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        container.exec_run,
                        cmd,
                        workdir="/workspace",
                        user="1000:1000",
                        environment=exec_env,
                        demux=True,
                    ),
                    timeout=timeout_s,
                )
            except TimeoutError:
                duration_ms = int((time.perf_counter() - start) * 1000)
                return ExecResult(
                    exit_code=124,
                    stdout="",
                    stderr=f"Command timed out after {timeout_s}s",
                    duration_ms=duration_ms,
                    timed_out=True,
                )

            exit_code = int(raw_result.exit_code)
            stdout_b, stderr_b = raw_result.output
            duration_ms = int((time.perf_counter() - start) * 1000)
            return ExecResult(
                exit_code=exit_code,
                stdout=(stdout_b or b"").decode("utf-8", errors="replace"),
                stderr=(stderr_b or b"").decode("utf-8", errors="replace"),
                duration_ms=duration_ms,
                timed_out=False,
            )
        finally:
            if egress_net is not None:
                await asyncio.to_thread(egress_net.disconnect, container)

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
        if heavy:
            async with self._semaphore.acquire(priority=priority):
                return await self._run_exec_sync(sandbox, cmd, network=network, timeout_s=timeout_s)
        return await self._run_exec_sync(sandbox, cmd, network=network, timeout_s=timeout_s)

    async def destroy(self, sandbox: Sandbox) -> None:
        client = self._get_client()
        try:
            container = await asyncio.to_thread(client.containers.get, sandbox.container_id)
            await asyncio.to_thread(container.remove, force=True)
        except Exception as exc:
            if type(exc).__name__ != "NotFound":
                raise

        volumes_api = getattr(client, "volumes", None)
        if volumes_api is not None:
            for vol_name in sandbox.ephemeral_volumes:
                try:
                    vol = await asyncio.to_thread(volumes_api.get, vol_name)
                    await asyncio.to_thread(vol.remove, force=True)
                except Exception as exc:
                    if type(exc).__name__ != "NotFound":
                        raise
