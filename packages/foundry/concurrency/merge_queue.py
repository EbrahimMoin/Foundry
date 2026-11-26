"""Serial rebase-and-test Merge Queue (G5 priority) + MERGED join barrier (A8-1)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from foundry.blackboard.models import TaskRecord, TaskStatus
from foundry.blackboard.repository import BlackboardRepository
from foundry.concurrency.reservations import WriteReservationManager
from foundry.config.schemas import BudgetConfig
from foundry.sandbox.protocol import ExecPriority, PackageInstallPolicy
from foundry.sandbox.semaphore import HeavyExecSemaphore
from foundry.sandbox.workspace_sync import sync_worktree_to_workspace
from foundry.security.hardened_git import GitError, HardenedGit
from foundry.security.workspace_guard import WorkspaceViolationError, validate_workspace_tree
from foundry.verification.models import GateReport


class UnapprovedDependencyError(ValueError):
    """Raised when pending_deps contains an unapproved or denylisted package (A7-10)."""


@runtime_checkable
class ConflictResolver(Protocol):
    """Tier-2 Integration Agent hook invoked only when git rebase hits a conflict."""

    async def resolve_conflicts(
        self,
        *,
        worktree_dir: Path,
        task: TaskRecord,
        conflicted_files: Sequence[str],
        main_sha: str,
    ) -> bool: ...


@runtime_checkable
class G5Verifier(Protocol):
    """Combined-code verification runner invoked inside SerialMergeQueue under G5 priority."""

    async def verify_combined(
        self,
        *,
        worktree_dir: Path,
        workspace_dir: Path,
        task: TaskRecord,
        base_sha: str,
        allowed_write_set: Sequence[str],
    ) -> GateReport: ...


@runtime_checkable
class SchemaTemplateRefresher(Protocol):
    """Refreshes app_template DB safely when a kind='schema' barrier task merges."""

    async def refresh(self, *, worktree_dir: Path, task: TaskRecord) -> None: ...


class MergeOutcome(BaseModel):
    """Result of processing a single VERIFIED task through the serial merge queue."""

    task_id: str
    status: Literal["merged", "requeued_rerun", "escalated", "failed"]
    merged_sha: str | None = None
    resolution_tier: Literal["clean", "integration_agent", "rerun", "failed"] = "clean"
    spawned_integration_task_id: str | None = None
    detail: str = ""


def _apply_pending_deps_to_manifests(
    worktree_dir: Path,
    pending_deps: Sequence[dict[str, Any]],
    package_policy: PackageInstallPolicy,
) -> list[str]:
    """Deterministically record approved pending_deps into repo manifests during Merge Queue."""
    if not pending_deps:
        return []

    touched: list[str] = []
    for entry in pending_deps:
        ecosystem = str(entry.get("ecosystem", "")).strip()
        pkg = str(entry.get("package", "")).strip()
        if not pkg or not package_policy.is_allowed(ecosystem, pkg):
            raise UnapprovedDependencyError(
                f"Dependency '{pkg}' ({ecosystem}) is neither curated nor human-approved."
            )

        if ecosystem == "python":
            pyproj = worktree_dir / "backend" / "pyproject.toml"
            if pyproj.is_file():
                text = pyproj.read_text(encoding="utf-8")
                pkg_base = PackageInstallPolicy.normalize_pkg_name(pkg)
                if pkg_base not in text.lower():
                    marker = "dependencies = ["
                    if marker in text:
                        text = text.replace(marker, f'{marker}\n    "{pkg}",', 1)
                    else:
                        text = text.rstrip() + f'\n# added dependency: "{pkg}"\n'
                    pyproj.write_text(text, encoding="utf-8")
                    if "backend/pyproject.toml" not in touched:
                        touched.append("backend/pyproject.toml")
        elif ecosystem == "npm":
            pkg_json = worktree_dir / "frontend" / "package.json"
            if pkg_json.is_file():
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                deps = data.setdefault("dependencies", {})
                pkg_base = PackageInstallPolicy.normalize_pkg_name(pkg)
                if pkg_base not in deps and pkg not in deps:
                    deps[pkg] = "*"
                    pkg_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                    if "frontend/package.json" not in touched:
                        touched.append("frontend/package.json")

    if touched:
        validate_workspace_tree(worktree_dir, ignore_git_dir=True)
    return touched


class SerialMergeQueue:
    """One-branch-at-a-time merge queue enforcing G5 priority, 3-tier conflict handling & A8-1."""

    def __init__(
        self,
        repo: BlackboardRepository,
        git: HardenedGit,
        semaphore: HeavyExecSemaphore,
        reservations: WriteReservationManager,
        *,
        repo_dir: Path,
        worktrees_root: Path,
        workspaces_root: Path,
        package_policy: PackageInstallPolicy | None = None,
        budget_cfg: BudgetConfig | None = None,
        conflict_resolver: ConflictResolver | None = None,
        g5_verifier: G5Verifier | None = None,
        schema_refresher: SchemaTemplateRefresher | None = None,
    ) -> None:
        self._repo = repo
        self._git = git
        self._semaphore = semaphore
        self._reservations = reservations
        self._repo_dir = repo_dir
        self._worktrees_root = worktrees_root
        self._workspaces_root = workspaces_root
        self._package_policy = package_policy or PackageInstallPolicy()
        self._budget_cfg = budget_cfg or BudgetConfig()
        self._conflict_resolver = conflict_resolver
        self._g5_verifier = g5_verifier
        self._schema_refresher = schema_refresher
        self._queue_lock = asyncio.Lock()

    async def _cleanup_branch_and_worktree(self, worktree_dir: Path, branch_name: str) -> None:
        if await asyncio.to_thread(worktree_dir.exists):
            with contextlib.suppress(GitError):
                await self._git.remove_worktree(self._repo_dir, worktree_dir)
        if await self._git.branch_exists(self._repo_dir, branch_name):
            with contextlib.suppress(GitError):
                await self._git.delete_branch(self._repo_dir, branch_name, force=True)

    async def _requeue_or_escalate(
        self,
        *,
        run_id: str,
        task: TaskRecord,
        worktree_dir: Path,
        branch_name: str,
        main_sha: str,
        reason: str,
    ) -> MergeOutcome:
        await self._cleanup_branch_and_worktree(worktree_dir, branch_name)
        await self._reservations.release(run_id, task.id, reason="released")
        fresh = await self._repo.get_task(task.id)
        if fresh.attempt + 1 < fresh.max_attempts:
            updated = await self._repo.set_task_status(
                fresh.id,
                TaskStatus.READY,
                fresh.version,
                attempt=fresh.attempt + 1,
                base_sha=main_sha,
                event_payload={"requeued_from_merge_queue": True, "reason": reason},
            )
            _ = updated
            return MergeOutcome(
                task_id=task.id,
                status="requeued_rerun",
                resolution_tier="rerun",
                detail=reason,
            )

        await self._repo.set_task_status(
            fresh.id,
            TaskStatus.ESCALATED,
            fresh.version,
            event_payload={"escalated_from_merge_queue": True, "reason": reason},
        )
        return MergeOutcome(
            task_id=task.id,
            status="escalated",
            resolution_tier="failed",
            detail=reason,
        )

    async def _maybe_spawn_join_integration(
        self, run_id: str, merged_task: TaskRecord, main_sha: str
    ) -> str | None:
        """Spawn kind='integration' ONLY after both backend & frontend reach MERGED (A8-1)."""
        if merged_task.wave != 2 or merged_task.kind != "build" or not merged_task.feature:
            return None

        feat = merged_task.feature
        wave2_tasks = await self._repo.list_tasks(run_id, wave=2)
        if any(t.kind == "integration" and t.feature == feat for t in wave2_tasks):
            return None

        feat_builds = [t for t in wave2_tasks if t.kind == "build" and t.feature == feat]
        by_domain = {t.domain: t for t in feat_builds}
        be = by_domain.get("backend")
        fe = by_domain.get("frontend")
        if be is None or fe is None:
            return None
        if be.status != TaskStatus.MERGED or fe.status != TaskStatus.MERGED:
            return None

        integration_task = await self._repo.create_task(
            run_id=run_id,
            wave=2,
            domain="integration",
            feature=feat,
            kind="integration",
            title=f"Integrate live frontend + backend for feature '{feat}'",
            description=(
                f"Connect frontend/src/features/{feat}/ to live backend/app/features/{feat}/ "
                "plus sbx_<task> Postgres DB and verify stateful end-to-end flows."
            ),
            status=TaskStatus.READY,
            acceptance=[f"Stateful API + UI flows for '{feat}' pass cleanly."],
            write_set=[
                f"backend/app/features/{feat}/",
                f"frontend/src/features/{feat}/",
            ],
            depends_on_task_ids=[be.id, fe.id],
            base_sha=main_sha,
            budget_implement_usd=self._budget_cfg.per_task.implement_usd,
            budget_review_usd=self._budget_cfg.per_task.review_usd,
        )
        await self._repo.upsert_feature(
            run_id,
            feat,
            status="verified",
            budget_blackbox_tests_usd=self._budget_cfg.per_feature.blackbox_tests_usd,
        )
        return integration_task.id

    async def process_task(self, run_id: str, task_id: str) -> MergeOutcome:
        """Process one VERIFIED task serially through rebase, diff guard, G5 & merge."""
        async with self._queue_lock:
            task = await self._repo.get_task(task_id)
            if task.status == TaskStatus.VERIFIED:
                task = await self._repo.set_task_status(
                    task.id,
                    TaskStatus.QUEUED,
                    task.version,
                )

            worktree_dir = (
                Path(task.worktree_path) if task.worktree_path else (self._worktrees_root / task.id)
            )
            workspace_dir = self._workspaces_root / task.id
            branch_name = task.branch or f"task/{task.id}"
            main_sha = await self._git.head_sha(self._repo_dir, "main")
            write_set = await self._repo.get_task_write_sets(task.id)
            allow_infra = task.domain == "infra"

            # Step 1: Validate & apply approved pending_deps
            try:
                extra_paths = _apply_pending_deps_to_manifests(
                    worktree_dir, task.pending_deps, self._package_policy
                )
            except UnapprovedDependencyError as exc:
                await self._reservations.release(run_id, task.id, reason="released")
                await self._repo.set_task_status(
                    task.id,
                    TaskStatus.ESCALATED,
                    task.version,
                    event_payload={"reason": str(exc)},
                )
                return MergeOutcome(
                    task_id=task.id,
                    status="escalated",
                    resolution_tier="failed",
                    detail=str(exc),
                )

            g3_test_paths: list[str] = []
            if task.feature:
                g3_test_paths = [
                    f"backend/tests/blackbox/{task.feature}/",
                    f"frontend/src/features/{task.feature}/__tests__/",
                ]
            allowed_write_set = [*write_set, *extra_paths, *g3_test_paths]
            if extra_paths:
                pending_paths = await self._git.status_paths(worktree_dir)
                if pending_paths:
                    await self._git.commit(
                        worktree_dir,
                        f"chore({task.id}): apply approved pending_deps",
                        allowed_write_set,
                        allow_infra=allow_infra,
                    )

            # Step 2: 3-tier rebase onto main (clean -> Integration Agent -> rerun)
            resolution_tier: Literal["clean", "integration_agent", "rerun", "failed"] = "clean"
            rebased, conflicts = await self._git.rebase_onto(worktree_dir, "main")
            if not rebased:
                resolved = False
                if self._conflict_resolver is not None:
                    resolved = await self._conflict_resolver.resolve_conflicts(
                        worktree_dir=worktree_dir,
                        task=task,
                        conflicted_files=conflicts,
                        main_sha=main_sha,
                    )
                    if resolved:
                        try:
                            rebased, conflicts = await self._git.continue_rebase(worktree_dir)
                        except (GitError, WorkspaceViolationError):
                            rebased = False
                if not rebased:
                    await self._git.abort_rebase(worktree_dir)
                    return await self._requeue_or_escalate(
                        run_id=run_id,
                        task=task,
                        worktree_dir=worktree_dir,
                        branch_name=branch_name,
                        main_sha=main_sha,
                        reason=f"Rebase conflict on {conflicts}",
                    )
                resolution_tier = "integration_agent"

            # Step 3: Symlink & diff write-set enforcement against main
            try:
                await self._git.enforce_diff_write_set(
                    worktree_dir,
                    main_sha,
                    allowed_write_set,
                    allow_infra=allow_infra,
                )
            except WorkspaceViolationError as exc:
                await self._reservations.release(run_id, task.id, reason="released")
                await self._repo.set_task_status(
                    task.id,
                    TaskStatus.ESCALATED,
                    task.version,
                    event_payload={"reason": str(exc)},
                )
                return MergeOutcome(
                    task_id=task.id,
                    status="escalated",
                    resolution_tier="failed",
                    detail=str(exc),
                )

            # Step 4: Sync rebased worktree -> .git-free workspace & run Gate G5 under priority 0
            sync_worktree_to_workspace(worktree_dir, workspace_dir)
            if self._g5_verifier is not None:
                async with self._semaphore.acquire(ExecPriority.MERGE_QUEUE_G5):
                    g5_report = await self._g5_verifier.verify_combined(
                        worktree_dir=worktree_dir,
                        workspace_dir=workspace_dir,
                        task=task,
                        base_sha=main_sha,
                        allowed_write_set=allowed_write_set,
                    )
                if not g5_report.passed:
                    return await self._requeue_or_escalate(
                        run_id=run_id,
                        task=task,
                        worktree_dir=worktree_dir,
                        branch_name=branch_name,
                        main_sha=main_sha,
                        reason=g5_report.summary(),
                    )

            # Step 5: Fast-forward main, refresh app_template on schema merge, release & emit
            new_main_sha = await self._git.fast_forward_branch(self._repo_dir, "main", branch_name)
            if task.kind == "schema" and self._schema_refresher is not None:
                await self._schema_refresher.refresh(worktree_dir=self._repo_dir, task=task)

            merged_task = await self._repo.set_task_status(
                task.id,
                TaskStatus.MERGED,
                task.version,
                event_payload={"merged_sha": new_main_sha, "resolution_tier": resolution_tier},
            )
            await self._reservations.release(run_id, task.id, reason="merged")
            await self._cleanup_branch_and_worktree(worktree_dir, branch_name)
            await self._repo.emit_event(
                run_id=run_id,
                event_type="main.updated",
                subject_id=task.id,
                payload={
                    "sha": new_main_sha,
                    "task_id": task.id,
                    "kind": task.kind,
                    "domain": task.domain,
                    "feature": task.feature,
                },
            )

            spawned_id = await self._maybe_spawn_join_integration(run_id, merged_task, new_main_sha)
            return MergeOutcome(
                task_id=task.id,
                status="merged",
                merged_sha=new_main_sha,
                resolution_tier=resolution_tier,
                spawned_integration_task_id=spawned_id,
                detail="Merged cleanly onto main",
            )
