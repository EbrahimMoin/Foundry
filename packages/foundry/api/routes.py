"""FastAPI REST endpoints backing the Foundry Daemon API and Dashboard v0.5 (Section 21.2)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request

from foundry.api.schemas import (
    AutonomyUpdate,
    ContractVersionsResponse,
    CostsSummaryResponse,
    DataPolicyUpdate,
    DecisionResolveRequest,
    FeatureMetricsSummary,
    MetricsSummaryResponse,
    ModelAssignmentsUpdate,
    ModelCostBucket,
    NoteCreateRequest,
    PreviewUrlResponse,
    ProjectSummary,
    RunControlResponse,
    RuntimeSettingsResponse,
    TaskDetailResponse,
)
from foundry.api.state import DaemonState
from foundry.blackboard.models import (
    AgentRecord,
    ContractRecord,
    DecisionRecord,
    LlmCallRecord,
    NoteRecord,
    RunRecord,
    TaskRecord,
    WriteReservationRecord,
)
from foundry.blackboard.repository import NotFoundError, StaleWriteError
from foundry.config.loader import (
    ConfigError,
    default_foundry_config,
    default_models_config,
    default_project_config,
    load_foundry_config,
    load_models_config,
    load_project_config,
    save_models_config,
    save_project_config,
)
from foundry.config.schemas import (
    AutonomyConfig,
    DataPolicyConfig,
    FoundryGlobalConfig,
    ModelsConfig,
    ProjectConfig,
    ProviderConfig,
)

api_router = APIRouter(prefix="/api", tags=["daemon"])


def _state(request: Request) -> DaemonState:
    return cast(DaemonState, request.app.state.daemon)


def _load_models(config_dir: Path) -> ModelsConfig:
    path = config_dir / "models.yaml"
    return load_models_config(path) if path.is_file() else default_models_config()


def _load_foundry(config_dir: Path) -> FoundryGlobalConfig:
    path = config_dir / "foundry.yaml"
    return load_foundry_config(path) if path.is_file() else default_foundry_config()


def _load_project(project_dir: Path) -> ProjectConfig:
    path = project_dir / ".foundry" / "project.yaml"
    return (
        load_project_config(path)
        if path.is_file()
        else default_project_config(name=project_dir.name)
    )


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@api_router.get("/projects", response_model=list[ProjectSummary])
async def list_projects(request: Request) -> list[ProjectSummary]:
    st = _state(request)
    proj_cfg = _load_project(st.project_dir)
    runs = await st.repo.list_runs()

    grouped: dict[str, list[RunRecord]] = defaultdict(list)
    for run in runs:
        grouped[run.project_id].append(run)

    summaries: list[ProjectSummary] = []
    seen: set[str] = set()
    for pid, pid_runs in sorted(grouped.items()):
        seen.add(pid)
        latest = pid_runs[0]
        summaries.append(
            ProjectSummary(
                id=pid,
                name=proj_cfg.name if pid == proj_cfg.name else pid,
                stack_pack=proj_cfg.stack_pack,
                run_count=len(pid_runs),
                latest_run_id=latest.id,
                latest_phase=latest.phase,
            )
        )

    if proj_cfg.name not in seen:
        summaries.append(
            ProjectSummary(
                id=proj_cfg.name,
                name=proj_cfg.name,
                stack_pack=proj_cfg.stack_pack,
                run_count=0,
            )
        )
    return summaries


@api_router.get("/projects/{project_id}", response_model=ProjectSummary)
async def get_project(project_id: str, request: Request) -> ProjectSummary:
    for item in await list_projects(request):
        if item.id == project_id or item.name == project_id:
            return item
    raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")


@api_router.get("/runs", response_model=list[RunRecord])
async def list_runs(
    request: Request,
    project_id: str | None = Query(default=None),
) -> list[RunRecord]:
    return await _state(request).repo.list_runs(project_id=project_id)


@api_router.get("/runs/{run_id}", response_model=RunRecord)
async def get_run(run_id: str, request: Request) -> RunRecord:
    try:
        return await _state(request).repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _transition_run(
    request: Request,
    run_id: str,
    phase: str,
    event_type: str,
    *,
    ended: bool = False,
) -> RunControlResponse:
    st = _state(request)
    try:
        updated = await st.repo.update_run_phase(run_id, phase, ended=ended)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await st.repo.emit_event(
        run_id=run_id,
        event_type=event_type,
        subject_id=run_id,
        payload={"run_id": run_id, "phase": updated.phase},
    )
    await st.relay.flush_once()
    return RunControlResponse(run_id=updated.id, phase=updated.phase, ended_at=updated.ended_at)


@api_router.post("/runs/{run_id}/pause", response_model=RunControlResponse)
async def pause_run(run_id: str, request: Request) -> RunControlResponse:
    return await _transition_run(request, run_id, "paused", "run.paused")


@api_router.post("/runs/{run_id}/resume", response_model=RunControlResponse)
async def resume_run(run_id: str, request: Request) -> RunControlResponse:
    return await _transition_run(request, run_id, "building", "run.resumed")


@api_router.post("/runs/{run_id}/stop", response_model=RunControlResponse)
async def stop_run(run_id: str, request: Request) -> RunControlResponse:
    return await _transition_run(request, run_id, "stopped", "run.stopped", ended=True)


@api_router.get("/runs/{run_id}/agents", response_model=list[AgentRecord])
async def list_agents(run_id: str, request: Request) -> list[AgentRecord]:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await st.repo.list_agents(run_id)


@api_router.get("/runs/{run_id}/tasks", response_model=list[TaskRecord])
async def list_tasks(
    run_id: str,
    request: Request,
    wave: int | None = Query(default=None),
) -> list[TaskRecord]:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await st.repo.list_tasks(run_id, wave=wave)


@api_router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task_detail(task_id: str, request: Request) -> TaskDetailResponse:
    st = _state(request)
    try:
        task = await st.repo.get_task(task_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_set = await st.repo.get_task_write_sets(task_id)
    deps = await st.repo.get_task_dependencies(task_id)
    active_res = [
        r for r in await st.repo.list_active_reservations(task.run_id) if r.task_id == task_id
    ]
    return TaskDetailResponse(
        task=task,
        write_set=write_set,
        dependencies=deps,
        reservations=active_res,
    )


@api_router.get("/runs/{run_id}/reservations", response_model=list[WriteReservationRecord])
async def list_reservations(
    run_id: str,
    request: Request,
    include_expired: bool = Query(default=False),
) -> list[WriteReservationRecord]:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await st.repo.list_active_reservations(run_id, include_expired=include_expired)


@api_router.get("/runs/{run_id}/contracts", response_model=list[ContractRecord])
async def list_contracts(run_id: str, request: Request) -> list[ContractRecord]:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await st.repo.list_contracts(run_id)


@api_router.get("/contracts/{contract_id}/versions", response_model=ContractVersionsResponse)
async def get_contract_versions(contract_id: str, request: Request) -> ContractVersionsResponse:
    st = _state(request)
    try:
        ctr = await st.repo.get_contract_by_id(contract_id)
        versions = await st.repo.list_contract_versions(contract_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContractVersionsResponse(
        contract_id=ctr.id,
        name=ctr.name,
        domain=ctr.domain,
        kind=ctr.kind,
        status=ctr.status,
        current_version=ctr.current_version,
        versions=versions,
    )


@api_router.get("/runs/{run_id}/decisions", response_model=list[DecisionRecord])
async def list_decisions(
    run_id: str,
    request: Request,
    status: str | None = Query(default=None),
) -> list[DecisionRecord]:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await st.repo.list_decisions(run_id=run_id, status=status)


@api_router.get("/runs/{run_id}/notes", response_model=list[NoteRecord])
async def list_run_notes(
    run_id: str,
    request: Request,
    feature: str | None = Query(default=None),
) -> list[NoteRecord]:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await st.repo.list_notes(run_id, feature=feature)


async def _create_run_note(
    run_id: str,
    body: NoteCreateRequest,
    request: Request,
) -> NoteRecord:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    cleaned = body.body.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Note body cannot be empty")

    note = await st.repo.post_note(
        run_id=run_id,
        kind=body.kind,
        body=cleaned,
        task_id=body.task_id,
        feature=body.feature,
    )
    await st.repo.emit_event(
        run_id=run_id,
        event_type="run.note_logged",
        subject_id=note.id,
        payload={
            "note_id": note.id,
            "kind": note.kind,
            "body": note.body,
            "task_id": note.task_id,
            "feature": note.feature,
        },
    )
    await st.relay.flush_once()
    return note


@api_router.post("/runs/{run_id}/notes", response_model=NoteRecord)
async def create_run_note(
    run_id: str,
    body: NoteCreateRequest,
    request: Request,
) -> NoteRecord:
    return await _create_run_note(run_id, body, request)


@api_router.post("/runs/{run_id}/chat", response_model=NoteRecord)
async def post_run_chat(
    run_id: str,
    body: NoteCreateRequest,
    request: Request,
) -> NoteRecord:
    return await _create_run_note(run_id, body, request)


@api_router.post("/decisions/{decision_id}/resolve", response_model=DecisionRecord)
async def resolve_decision(
    decision_id: str,
    body: DecisionResolveRequest,
    request: Request,
) -> DecisionRecord:
    st = _state(request)
    choice = body.choice if body.choice is not None else body.answer
    try:
        resolved = await st.decisions.resolve(
            decision_id,
            action=body.action,
            choice=choice,
            feedback=body.feedback,
            decided_by=body.decided_by,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StaleWriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await st.relay.flush_once()
    return resolved


def _bucket_calls(
    calls: list[LlmCallRecord],
    key_fn: Any,
) -> list[ModelCostBucket]:
    grouped: dict[str, list[LlmCallRecord]] = defaultdict(list)
    for call in calls:
        grouped[str(key_fn(call))].append(call)
    buckets: list[ModelCostBucket] = []
    for key, items in sorted(grouped.items()):
        buckets.append(
            ModelCostBucket(
                key=key,
                calls=len(items),
                input_tokens=sum(c.input_tokens for c in items),
                cached_tokens=sum(c.cached_tokens for c in items),
                output_tokens=sum(c.output_tokens for c in items),
                cost_usd=round(sum(c.cost_usd for c in items), 6),
                est_value_usd=round(sum(c.est_value_usd for c in items), 6),
            )
        )
    return buckets


@api_router.get("/runs/{run_id}/costs", response_model=CostsSummaryResponse)
async def get_run_costs(run_id: str, request: Request) -> CostsSummaryResponse:
    st = _state(request)
    try:
        run = await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    calls = await st.repo.list_llm_calls(run_id)
    total_in = sum(c.input_tokens for c in calls)
    total_cached = sum(c.cached_tokens for c in calls)
    hit_rate = round(total_cached / total_in, 4) if total_in > 0 else 0.0
    return CostsSummaryResponse(
        run_id=run_id,
        budget_usd=run.budget_usd,
        spent_usd=run.spent_usd,
        total_call_cost_usd=round(sum(c.cost_usd for c in calls), 6),
        total_est_value_usd=round(sum(c.est_value_usd for c in calls), 6),
        call_count=len(calls),
        cache_hit_rate=hit_rate,
        by_model=_bucket_calls(calls, lambda c: f"{c.provider}/{c.model}"),
        by_profile=_bucket_calls(calls, lambda c: c.profile),
        calls=calls,
    )


@api_router.get("/runs/{run_id}/metrics", response_model=MetricsSummaryResponse)
async def get_run_metrics(run_id: str, request: Request) -> MetricsSummaryResponse:
    st = _state(request)
    try:
        run = await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    tasks = await st.repo.list_tasks(run_id)
    reservations = await st.repo.list_active_reservations(run_id)
    contracts = await st.repo.list_contracts(run_id)
    pending_decisions = await st.repo.list_decisions(run_id=run_id, status="pending")
    features = await st.repo.list_features(run_id)
    events = await st.repo.list_events_after(0, run_id=run_id)

    by_status: dict[str, int] = defaultdict(int)
    for t in tasks:
        by_status[t.status.value] += 1

    return MetricsSummaryResponse(
        run_id=run_id,
        phase=run.phase,
        tasks_total=len(tasks),
        tasks_by_status=dict(sorted(by_status.items())),
        total_attempts=sum(t.attempt for t in tasks),
        total_contract_reopens=sum(t.contract_reopens for t in tasks),
        active_reservations=len(reservations),
        contracts_count=len(contracts),
        pending_decisions=len(pending_decisions),
        dispute_rounds_total=sum(f.dispute_rounds for f in features),
        features=[
            FeatureMetricsSummary(
                name=f.name,
                status=f.status,
                budget_blackbox_tests_usd=f.budget_blackbox_tests_usd,
                spent_blackbox_tests_usd=f.spent_blackbox_tests_usd,
                dispute_rounds=f.dispute_rounds,
            )
            for f in features
        ],
        events_count=len(events),
    )


@api_router.get("/settings/providers", response_model=dict[str, ProviderConfig])
async def get_settings_providers(request: Request) -> dict[str, ProviderConfig]:
    return _load_models(_state(request).config_dir).providers


@api_router.get("/settings/models", response_model=ModelsConfig)
async def get_settings_models(request: Request) -> ModelsConfig:
    return _load_models(_state(request).config_dir)


@api_router.put("/settings/models/assignments", response_model=ModelsConfig)
async def update_model_assignments(
    body: ModelAssignmentsUpdate,
    request: Request,
) -> ModelsConfig:
    st = _state(request)
    cfg = _load_models(st.config_dir)
    merged_roles = {**cfg.assignments.roles, **body.roles}
    try:
        updated = ModelsConfig(
            providers=cfg.providers,
            profiles=cfg.profiles,
            assignments=cfg.assignments.model_copy(update={"roles": merged_roles}),
            policies=cfg.policies,
        )
        save_models_config(updated, st.config_dir / "models.yaml")
    except (ValueError, ConfigError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return updated


@api_router.get("/settings/autonomy", response_model=AutonomyConfig)
async def get_settings_autonomy(request: Request) -> AutonomyConfig:
    return _load_project(_state(request).project_dir).autonomy


@api_router.put("/settings/autonomy", response_model=AutonomyConfig)
async def update_settings_autonomy(body: AutonomyUpdate, request: Request) -> AutonomyConfig:
    st = _state(request)
    proj = _load_project(st.project_dir)
    updates: dict[str, Any] = {}
    if body.preset is not None:
        updates["preset"] = body.preset
    if body.overrides is not None:
        updates["overrides"] = body.overrides
    if body.confidence_threshold is not None:
        updates["confidence_threshold"] = body.confidence_threshold
    new_autonomy = proj.autonomy.model_copy(update=updates)
    updated_proj = proj.model_copy(update={"autonomy": new_autonomy})
    save_project_config(updated_proj, st.project_dir / ".foundry" / "project.yaml")
    return new_autonomy


@api_router.get("/settings/data-policy", response_model=DataPolicyConfig)
async def get_settings_data_policy(request: Request) -> DataPolicyConfig:
    return _load_project(_state(request).project_dir).data_policy


@api_router.put("/settings/data-policy", response_model=DataPolicyConfig)
async def update_settings_data_policy(
    body: DataPolicyUpdate,
    request: Request,
) -> DataPolicyConfig:
    st = _state(request)
    proj = _load_project(st.project_dir)
    new_policy = DataPolicyConfig(
        allow_free_tier=(
            body.allow_free_tier
            if body.allow_free_tier is not None
            else proj.data_policy.allow_free_tier
        ),
        allow_trace_export=(
            body.allow_trace_export
            if body.allow_trace_export is not None
            else proj.data_policy.allow_trace_export
        ),
    )
    updated_proj = proj.model_copy(update={"data_policy": new_policy})
    save_project_config(updated_proj, st.project_dir / ".foundry" / "project.yaml")
    return new_policy


@api_router.get("/settings/runtime", response_model=RuntimeSettingsResponse)
async def get_settings_runtime(request: Request) -> RuntimeSettingsResponse:
    cfg = _load_foundry(_state(request).config_dir)
    return RuntimeSettingsResponse(
        server=cfg.server,
        runtime=cfg.runtime,
        embeddings=cfg.embeddings,
        observability=cfg.observability,
        budgets=cfg.budgets,
        secrets_backends=list(cfg.secrets.backend_order),
    )


@api_router.get("/preview/{run_id}/url", response_model=PreviewUrlResponse)
async def get_preview_url(
    run_id: str,
    request: Request,
    env: Literal["local", "staging", "production"] = Query(default="local"),
) -> PreviewUrlResponse:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    run_urls = st.preview_urls.get(run_id, {})
    resolved = run_urls.get(env)
    if resolved is None and env == "local":
        resolved = st.default_local_preview_url
    return PreviewUrlResponse(
        run_id=run_id,
        env=env,
        url=resolved,
        available=resolved is not None,
    )
