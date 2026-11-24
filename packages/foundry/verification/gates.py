"""Sequential G1 (self-check) and G2 (task gate) verification runner."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from foundry.sandbox.protocol import ExecPriority, Sandbox, SandboxProvider
from foundry.sandbox.semaphore import HeavyExecSemaphore
from foundry.sandbox.workspace_sync import sync_workspace_to_worktree
from foundry.security.hardened_git import HardenedGit
from foundry.security.scanner import SecretScanner
from foundry.security.workspace_guard import WorkspaceViolationError, validate_workspace_tree
from foundry.stack_packs.protocol import StackPack
from foundry.verification.contract_checks import (
    OpenAPIExportError,
    check_pinned_pointers,
    check_pinned_testids,
    diff_openapi_breaking,
    load_exported_openapi,
    run_optional_host_scanners,
    scan_workspace_secrets,
)
from foundry.verification.models import (
    CheckOutcome,
    ContractViolation,
    GateName,
    GateReport,
)

_OPENAPI_REL_PATH = ".foundry_tmp/openapi.json"

_OPENAPI_EXPORT_CMD: list[str] = [
    "python3",
    "-c",
    (
        "import json, pathlib, sys; "
        "sys.path.insert(0, 'backend'); "
        "from app.main import app; "
        "p = pathlib.Path('.foundry_tmp/openapi.json'); "
        "p.parent.mkdir(parents=True, exist_ok=True); "
        "p.write_text(json.dumps(app.openapi(), indent=2), encoding='utf-8')"
    ),
]

_SCHEMATHESIS_SCRIPT = (
    "import sys; sys.path.insert(0, 'backend'); "
    "from app.main import app; "
    "import hypothesis, schemathesis; "
    "schema = schemathesis.openapi.from_asgi('/openapi.json', app); "
    "checks = ("
    "schemathesis.checks.not_a_server_error, "
    "schemathesis.checks.status_code_conformance, "
    "schemathesis.checks.response_schema_conformance"
    "); "
    "settings = hypothesis.settings("
    "max_examples=10, database=None, deadline=None, "
    "suppress_health_check=[hypothesis.HealthCheck.too_slow]"
    "); "
    "ops = [res.ok() for res in schema.get_all_operations()]; "
    "[hypothesis.seed(42)(hypothesis.given(case=op.as_strategy())"
    "(settings(lambda case: (case.call_and_validate(checks=checks), None)[-1])))() "
    "for op in ops]; "
    "print(f'schemathesis validated {len(ops)} operations (seed=42, max_examples=10)')"
)

_SCHEMATHESIS_CMD: list[str] = ["python3", "-c", _SCHEMATHESIS_SCRIPT]


class GateRunner:
    """Runs Gate G1 and Gate G2 sequentially inside Docker plus safe host checks."""

    def __init__(
        self,
        provider: SandboxProvider,
        sandbox: Sandbox,
        *,
        stack_pack: StackPack,
        git: HardenedGit | None = None,
        worktree_dir: Path | None = None,
        write_set: Sequence[str] = (),
        pinned_openapi: dict[str, Any] | None = None,
        baseline_openapi_path: Path | None = None,
        pinned_testids: Sequence[str] = (),
        run_frontend: bool = False,
        run_schemathesis: bool = True,
        scanner: SecretScanner | None = None,
        allow_infra: bool = False,
        semaphore: HeavyExecSemaphore | None = None,
    ) -> None:
        self._provider = provider
        self._sandbox = sandbox
        self._stack_pack = stack_pack
        self._git = git
        self._worktree_dir = worktree_dir
        self._write_set = list(write_set)
        self._pinned_openapi = pinned_openapi
        self._baseline_openapi_path = baseline_openapi_path
        self._pinned_testids = list(pinned_testids)
        self._run_frontend = run_frontend
        self._run_schemathesis = run_schemathesis
        self._scanner = scanner or SecretScanner()
        self._allow_infra = allow_infra
        self._semaphore = semaphore

    def _sandbox_checks(
        self,
        *,
        g2: bool,
        full_suite: bool = False,
    ) -> list[tuple[str, list[str], bool]]:
        backend_targets = (
            ["backend/"] if full_suite else [p for p in self._write_set if p.startswith("backend/")]
        )
        ruff_targets = backend_targets if backend_targets else ["backend/"]
        mypy_targets = (
            ["backend/app"]
            if full_suite
            else [
                p for p in backend_targets if p != "backend/" and not p.startswith("backend/tests")
            ]
        )
        if not mypy_targets:
            mypy_targets = ["backend/app"]

        checks: list[tuple[str, list[str], bool]] = [
            ("ruff", ["ruff", "check", *ruff_targets], True),
            (
                "mypy",
                ["mypy", "--config-file", "backend/pyproject.toml", *mypy_targets],
                True,
            ),
            (
                "pytest",
                [
                    "pytest",
                    "-q",
                    "-o",
                    "cache_dir=/tmp/.pytest_cache",
                    "-o",
                    "asyncio_mode=auto",
                    "backend/tests",
                ],
                True,
            ),
            ("openapi_export", _OPENAPI_EXPORT_CMD, True),
        ]

        if self._run_schemathesis:
            # Advisory in G1, blocking in G2 (A7-7)
            checks.append(("schemathesis", _SCHEMATHESIS_CMD, g2))

        if self._run_frontend:
            checks.extend(
                [
                    ("tsc", ["pnpm", "--dir", "frontend", "exec", "tsc", "--noEmit"], True),
                    ("vitest", ["pnpm", "--dir", "frontend", "test", "--run"], True),
                ]
            )
        return checks

    def _run_host_contract_checks(self) -> tuple[list[ContractViolation], str | None]:
        violations: list[ContractViolation] = []
        exported_abs = self._sandbox.workspace_dir / _OPENAPI_REL_PATH

        try:
            exported_doc = load_exported_openapi(
                self._sandbox.workspace_dir,
                rel_path=_OPENAPI_REL_PATH,
            )
        except (WorkspaceViolationError, OpenAPIExportError) as exc:
            violations.append(
                ContractViolation(
                    kind="schema_error",
                    pointer=_OPENAPI_REL_PATH,
                    message=str(exc),
                )
            )
            return violations, None

        covered_pointers: set[tuple[str, str]] = set()
        if self._pinned_openapi is not None:
            for v in check_pinned_pointers(self._pinned_openapi, exported_doc):
                violations.append(v)
                covered_pointers.add((v.pointer, v.message))

        baseline_doc: dict[str, Any] | None = None
        baseline_file: Path | None = None
        if self._baseline_openapi_path is not None and self._baseline_openapi_path.is_file():
            validate_workspace_tree(self._baseline_openapi_path, ignore_git_dir=False)
            loaded = yaml.safe_load(self._baseline_openapi_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                baseline_doc = loaded
                baseline_file = self._baseline_openapi_path

        if baseline_doc is not None:
            for v in diff_openapi_breaking(
                baseline_doc,
                exported_doc,
                baseline_path=baseline_file,
                exported_path=exported_abs,
            ):
                # Avoid duplicate errors for missing paths/methods already reported as pinned
                is_dup_missing = any(
                    v.pointer == ptr or v.pointer.startswith(f"{ptr}/")
                    for ptr, _ in covered_pointers
                    if "added newly required" not in v.message
                )
                if not is_dup_missing:
                    violations.append(v)

        return violations, str(exported_abs)

    async def _run_sandbox_sequence(
        self,
        *,
        g2: bool,
        priority: ExecPriority = ExecPriority.IMPLEMENT_G1_G2,
        full_suite: bool = False,
    ) -> tuple[list[CheckOutcome], bool]:
        outcomes: list[CheckOutcome] = []
        aborted = False
        for name, cmd, blocking in self._sandbox_checks(g2=g2, full_suite=full_suite):
            res = await self._provider.exec(
                self._sandbox,
                cmd,
                network=False,
                heavy=True,
                priority=priority,
            )
            passed = res.exit_code == 0 and not res.timed_out
            outcomes.append(
                CheckOutcome(
                    name=name,
                    passed=passed,
                    blocking=blocking,
                    exit_code=res.exit_code,
                    duration_ms=res.duration_ms,
                    detail="passed" if passed else f"exit={res.exit_code}",
                    stdout=res.stdout,
                    stderr=res.stderr,
                )
            )
            if not passed and blocking:
                aborted = True
                break
        return outcomes, aborted

    async def _run_host_scanners_locked(
        self, scan_target: Path, *, priority: ExecPriority
    ) -> list[CheckOutcome]:
        if self._semaphore is not None:
            async with self._semaphore.acquire(priority=priority):
                return await asyncio.to_thread(run_optional_host_scanners, scan_target)
        return await asyncio.to_thread(run_optional_host_scanners, scan_target)

    async def run_g1(self) -> GateReport:
        """Execute Gate G1: sequential write-set checks + OpenAPI contract validation."""
        await asyncio.to_thread(
            validate_workspace_tree,
            self._sandbox.workspace_dir,
            ignore_git_dir=False,
        )
        checks, aborted = await self._run_sandbox_sequence(g2=False)

        violations: list[ContractViolation] = []
        exported_path: str | None = None
        if not aborted or any(c.name == "openapi_export" and c.passed for c in checks):
            violations, exported_path = await asyncio.to_thread(self._run_host_contract_checks)

        blocking_ok = all(c.passed for c in checks if c.blocking)
        return GateReport(
            gate=GateName.G1,
            passed=blocking_ok and not violations,
            checks=checks,
            violations=violations,
            exported_openapi_path=exported_path,
        )

    async def run_g2(self) -> GateReport:
        """Execute Gate G2: symlink/write-set guard + full checks + pinned & secret checks."""
        checks: list[CheckOutcome] = []
        violations: list[ContractViolation] = []

        # 1. Symlink/FIFO pre-walk + write-set diff enforcement before tests/commit
        try:
            await asyncio.to_thread(
                validate_workspace_tree,
                self._sandbox.workspace_dir,
                ignore_git_dir=False,
            )
            if self._worktree_dir is not None and self._write_set:
                await asyncio.to_thread(
                    sync_workspace_to_worktree,
                    self._sandbox.workspace_dir,
                    self._worktree_dir,
                    self._write_set,
                    allow_infra=self._allow_infra,
                )
                if self._git is not None:
                    await self._git.enforce_write_set(
                        self._worktree_dir,
                        self._write_set,
                        allow_infra=self._allow_infra,
                    )
            checks.append(CheckOutcome(name="workspace_guard", passed=True, detail="passed"))
        except Exception as exc:
            checks.append(
                CheckOutcome(
                    name="workspace_guard",
                    passed=False,
                    exit_code=1,
                    detail=str(exc),
                    stderr=str(exc),
                )
            )
            return GateReport(gate=GateName.G2, passed=False, checks=checks, violations=violations)

        # 2. Sequential sandbox checks (with blocking Schemathesis)
        sandbox_outcomes, aborted = await self._run_sandbox_sequence(g2=True)
        checks.extend(sandbox_outcomes)

        # 3. Host-side static OpenAPI, test-ID, secret, and optional Semgrep/gitleaks scans
        exported_path: str | None = None
        if not aborted:
            contract_violations, exported_path = await asyncio.to_thread(
                self._run_host_contract_checks
            )
            violations.extend(contract_violations)

        if self._pinned_testids:
            violations.extend(
                await asyncio.to_thread(
                    check_pinned_testids,
                    self._sandbox.workspace_dir,
                    self._pinned_testids,
                )
            )

        violations.extend(
            await asyncio.to_thread(
                scan_workspace_secrets,
                self._sandbox.workspace_dir,
                self._scanner,
            )
        )

        scan_target = self._worktree_dir or self._sandbox.workspace_dir
        checks.extend(
            await self._run_host_scanners_locked(
                scan_target,
                priority=ExecPriority.IMPLEMENT_G1_G2,
            )
        )

        blocking_ok = all(c.passed for c in checks if c.blocking)
        return GateReport(
            gate=GateName.G2,
            passed=blocking_ok and not violations,
            checks=checks,
            violations=violations,
            exported_openapi_path=exported_path,
        )

    async def run_g5(
        self,
        *,
        base_sha: str | None = None,
        extra_allowed_paths: Sequence[str] = (),
    ) -> GateReport:
        """Execute Gate G5: merge-queue combined-code verification with top semaphore priority."""
        checks: list[CheckOutcome] = []
        violations: list[ContractViolation] = []
        allowed_set = [*self._write_set, *extra_allowed_paths]

        try:
            await asyncio.to_thread(
                validate_workspace_tree,
                self._sandbox.workspace_dir,
                ignore_git_dir=False,
            )
            if (
                self._git is not None
                and self._worktree_dir is not None
                and base_sha
                and allowed_set
            ):
                await self._git.enforce_diff_write_set(
                    self._worktree_dir,
                    base_sha,
                    allowed_set,
                    allow_infra=self._allow_infra,
                )
            checks.append(CheckOutcome(name="merge_diff_guard", passed=True, detail="passed"))
        except Exception as exc:
            checks.append(
                CheckOutcome(
                    name="merge_diff_guard",
                    passed=False,
                    exit_code=1,
                    detail=str(exc),
                    stderr=str(exc),
                )
            )
            return GateReport(gate=GateName.G5, passed=False, checks=checks, violations=violations)

        sandbox_outcomes, aborted = await self._run_sandbox_sequence(
            g2=True,
            priority=ExecPriority.MERGE_QUEUE_G5,
            full_suite=True,
        )
        checks.extend(sandbox_outcomes)

        exported_path: str | None = None
        if not aborted:
            contract_violations, exported_path = await asyncio.to_thread(
                self._run_host_contract_checks
            )
            violations.extend(contract_violations)

        violations.extend(
            await asyncio.to_thread(
                scan_workspace_secrets,
                self._sandbox.workspace_dir,
                self._scanner,
            )
        )

        scan_target = self._worktree_dir or self._sandbox.workspace_dir
        checks.extend(
            await self._run_host_scanners_locked(
                scan_target,
                priority=ExecPriority.MERGE_QUEUE_G5,
            )
        )

        blocking_ok = all(c.passed for c in checks if c.blocking)
        return GateReport(
            gate=GateName.G5,
            passed=blocking_ok and not violations,
            checks=checks,
            violations=violations,
            exported_openapi_path=exported_path,
        )
