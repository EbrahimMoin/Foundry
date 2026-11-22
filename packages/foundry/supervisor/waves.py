"""Wave 1 Foundation -> Wave 2 Feature planning and dependency-barrier enforcement."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from foundry.blackboard.models import TaskRecord, TaskStatus
from foundry.blackboard.repository import BlackboardRepository
from foundry.config.schemas import BudgetConfig
from foundry.stack_packs.protocol import StackPack

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


class WavePlanTask(BaseModel):
    """Planned task specification before Blackboard materialization."""

    key: str
    wave: int
    domain: str
    feature: str | None = None
    kind: str = "build"
    title: str
    description: str
    write_set: list[str] = Field(default_factory=list)
    depends_on_keys: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)


def _slug(raw: str) -> str:
    cleaned = _SLUG_RE.sub("_", raw.strip().lower()).strip("_")
    return cleaned or "core"


def plan_wave1_foundation(
    *,
    app_name: str,
    stack_pack: StackPack,
) -> list[WavePlanTask]:
    """Generate Wave 1 Foundation tasks F1 -> F2 -> F3 || F4 (Section 9.2)."""
    _ = stack_pack
    return [
        WavePlanTask(
            key="F1",
            wave=1,
            domain="infra",
            feature=None,
            kind="deps",
            title=f"Wave 1 F1: Initialize {app_name} deps, CI & compose skeleton",
            description="Verify Wave 0 pyproject.toml, package.json, and compose setup.",
            write_set=["infra/", ".github/workflows/", "backend/pyproject.toml"],
            depends_on_keys=[],
            acceptance=["Wave 0 skeleton dependencies and CI config verified."],
        ),
        WavePlanTask(
            key="F2",
            wave=1,
            domain="data",
            feature=None,
            kind="schema",
            title="Wave 1 F2: SQLAlchemy models + 0001_initial migration",
            description=(
                "Create centralized SQLAlchemy models in backend/app/models/ and Alembic "
                "0001_initial migration matching locked db_schema contract."
            ),
            write_set=["backend/app/models/", "backend/migrations/"],
            depends_on_keys=["F1"],
            acceptance=["Centralized SQLAlchemy models and 0001_initial migration valid."],
        ),
        WavePlanTask(
            key="F3",
            wave=1,
            domain="backend",
            feature="auth",
            kind="build",
            title="Wave 1 F3: Auth endpoints, get_current_user & conftest seed_user(role=...)",
            description=(
                "Implement auth router (register/login/me), shared security dependency, "
                "and backend/tests/conftest.py seed_user(role=...) helper."
            ),
            write_set=["backend/app/core/", "backend/app/features/auth/", "backend/tests/"],
            depends_on_keys=["F2"],
            acceptance=[
                "POST /api/auth/login returns bearer token; seed_user(role=...) works in conftest."
            ],
        ),
        WavePlanTask(
            key="F4",
            wave=1,
            domain="frontend",
            feature="shell",
            kind="build",
            title="Wave 1 F4: AppLayout nav registry, AuthProvider & OpenAPI client shell",
            description=(
                "Implement AppLayout reading feature.ts registries, AuthProvider, and "
                "static OpenAPI-validated fixture helpers."
            ),
            write_set=["frontend/src/app/", "frontend/src/components/ui/", "frontend/src/api/"],
            depends_on_keys=["F2"],
            acceptance=["AppLayout renders dynamic feature.ts navigation cleanly."],
        ),
    ]


def _extract_feature_names(
    requirements: dict[str, Any] | None,
    architecture: dict[str, Any] | None,
    api_contract: dict[str, Any] | None,
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for src in (architecture, requirements):
        if isinstance(src, dict) and isinstance(src.get("features"), list):
            for entry in src["features"]:
                raw_name = entry.get("name") if isinstance(entry, dict) else str(entry)
                if raw_name:
                    slug = _slug(str(raw_name))
                    if slug not in {"auth", "health", "shell"} and slug not in seen:
                        seen.add(slug)
                        names.append(slug)

    if not names and isinstance(api_contract, dict):
        paths = api_contract.get("paths", {})
        if isinstance(paths, dict):
            for path_key in paths:
                parts = [p for p in str(path_key).split("/") if p]
                if len(parts) >= 2 and parts[0] == "api":
                    feat = _slug(parts[1])
                    if feat not in {"health", "auth"} and feat not in seen:
                        seen.add(feat)
                        names.append(feat)

    return names or ["core"]


def plan_wave2_features(
    *,
    requirements: dict[str, Any] | None,
    architecture: dict[str, Any] | None,
    api_contract: dict[str, Any] | None,
    wave1_keys: Sequence[str],
    stack_pack: StackPack,
) -> list[WavePlanTask]:
    """Generate Wave 2 feature tasks (all gated behind Wave 1) + linear schema barriers (A7-9)."""
    tasks: list[WavePlanTask] = []
    foundation_deps = list(wave1_keys)

    # Any Wave 2 schema migrations execute as serialized barriers chained via `blocks`
    schema_barrier_key: str | None = None
    raw_schema_changes = (
        architecture.get("schema_changes", []) if isinstance(architecture, dict) else []
    )
    if isinstance(raw_schema_changes, list):
        for idx, desc in enumerate(raw_schema_changes, start=1):
            if not str(desc).strip():
                continue
            key = f"W2_SCHEMA_{idx}"
            deps = [schema_barrier_key] if schema_barrier_key else list(foundation_deps)
            tasks.append(
                WavePlanTask(
                    key=key,
                    wave=2,
                    domain="data",
                    feature=None,
                    kind="schema",
                    title=f"Wave 2 Schema Barrier #{idx}: {str(desc)[:60]}",
                    description=str(desc),
                    write_set=stack_pack.default_write_set("data", kind="schema"),
                    depends_on_keys=deps,
                    acceptance=["Expand-only Alembic migration cleanly passes G2/G5."],
                )
            )
            schema_barrier_key = key

    feat_deps = [schema_barrier_key] if schema_barrier_key else list(foundation_deps)
    feature_names = _extract_feature_names(requirements, architecture, api_contract)
    for feat in feature_names:
        tasks.append(
            WavePlanTask(
                key=f"W2_BE_{feat}",
                wave=2,
                domain="backend",
                feature=feat,
                kind="build",
                title=f"Implement backend feature: {feat}",
                description=f"Implement backend router/schemas/service/tests for '{feat}'.",
                write_set=stack_pack.default_write_set("backend", feat),
                depends_on_keys=list(feat_deps),
                acceptance=[f"Backend endpoints and unit tests for '{feat}' pass G1/G2."],
            )
        )
        tasks.append(
            WavePlanTask(
                key=f"W2_FE_{feat}",
                wave=2,
                domain="frontend",
                feature=feat,
                kind="build",
                title=f"Implement frontend feature: {feat}",
                description=f"Implement frontend routes/components/testids for '{feat}'.",
                write_set=stack_pack.default_write_set("frontend", feat),
                depends_on_keys=list(feat_deps),
                acceptance=[f"Frontend components for '{feat}' expose pinned data-testids."],
            )
        )

    return tasks


async def materialize_wave_tasks(
    repo: BlackboardRepository,
    *,
    run_id: str,
    planned_tasks: Sequence[WavePlanTask],
    budget_cfg: BudgetConfig,
    key_to_id: dict[str, str] | None = None,
) -> tuple[list[TaskRecord], dict[str, str]]:
    """Persist planned tasks into Blackboard, setting READY vs BLOCKED by met dependencies."""
    mapping = dict(key_to_id or {})
    existing_tasks = await repo.list_tasks(run_id)
    merged_ids = {t.id for t in existing_tasks if t.status == TaskStatus.MERGED}
    created: list[TaskRecord] = []

    for spec in planned_tasks:
        dep_ids = [mapping[k] for k in spec.depends_on_keys if k in mapping]
        all_deps_merged = all(did in merged_ids for did in dep_ids)
        initial_status = (
            TaskStatus.READY if (not dep_ids or all_deps_merged) else TaskStatus.BLOCKED
        )
        task = await repo.create_task(
            run_id=run_id,
            wave=spec.wave,
            domain=spec.domain,
            feature=spec.feature,
            kind=spec.kind,
            title=spec.title,
            description=spec.description,
            status=initial_status,
            acceptance=spec.acceptance,
            write_set=spec.write_set,
            depends_on_task_ids=dep_ids,
            budget_implement_usd=budget_cfg.per_task.implement_usd,
            budget_review_usd=budget_cfg.per_task.review_usd,
        )
        mapping[spec.key] = task.id
        created.append(task)

    return created, mapping


async def advance_ready_tasks(repo: BlackboardRepository, run_id: str) -> list[TaskRecord]:
    """Promote BLOCKED tasks whose deps are MERGED to READY (enforcing Wave 1 -> Wave 2)."""
    all_tasks = await repo.list_tasks(run_id)
    by_id = {t.id: t for t in all_tasks}
    wave1_tasks = [t for t in all_tasks if t.wave == 1]
    wave1_complete = bool(wave1_tasks) and all(t.status == TaskStatus.MERGED for t in wave1_tasks)

    promoted: list[TaskRecord] = []
    for task in all_tasks:
        if task.status != TaskStatus.BLOCKED:
            continue
        if task.wave >= 2 and not wave1_complete:
            continue
        dep_ids = await repo.get_task_dependencies(task.id)
        if all(dep_id in by_id and by_id[dep_id].status == TaskStatus.MERGED for dep_id in dep_ids):
            updated = await repo.set_task_status(
                task.id,
                TaskStatus.READY,
                task.version,
                event_payload={"wave": task.wave, "promoted_from": "BLOCKED"},
            )
            by_id[task.id] = updated
            promoted.append(updated)
    return promoted
