"""FastAPI REST endpoints backing the Foundry Daemon API and Dashboard v0.5 (Section 21.2)."""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request
from ulid import ULID

from foundry.api.schemas import (
    AgentSlotCreateRequest,
    AgentSlotUpdateRequest,
    ApiKeyCreateRequest,
    ApiKeyPublicRecord,
    ApiKeysStatusResponse,
    AutonomyUpdate,
    ChatMessageCreateRequest,
    ChatRunCreatedResponse,
    ChatSessionSummary,
    ContractVersionsResponse,
    CostsSummaryResponse,
    CreateRunRequest,
    DataPolicyUpdate,
    DecisionResolveRequest,
    FeatureMetricsSummary,
    KeyModelsResponse,
    MetricsSummaryResponse,
    ModelAssignmentsUpdate,
    ModelCostBucket,
    ModelProfileUpsertRequest,
    NoteCreateRequest,
    OpenAISubscriptionStatusResponse,
    OpenAISubscriptionUpdateRequest,
    PreviewUrlResponse,
    ProjectSummary,
    ProviderCatalogResponse,
    PurgeTestRunsResponse,
    RunAgentCreateRequest,
    RunControlResponse,
    RunDeleteResponse,
    RuntimeSettingsResponse,
    TaskDetailResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    VaultUnlockRequest,
)
from foundry.api.state import DaemonState
from foundry.blackboard.models import (
    AgentRecord,
    ContractRecord,
    DecisionRecord,
    LlmCallRecord,
    MessageRecord,
    NoteRecord,
    RunRecord,
    TaskRecord,
    WriteReservationRecord,
)
from foundry.blackboard.repository import NotFoundError, StaleWriteError
from foundry.config.catalog import (
    discover_provider_models,
    get_provider_catalog_entry,
    is_model_compatible_with_provider,
    list_provider_catalog,
    provider_config_from_catalog,
)
from foundry.config.loader import (
    ConfigError,
    default_agent_roster_config,
    default_api_keys_registry,
    default_foundry_config,
    default_models_config,
    default_project_config,
    ensure_subscription_provider,
    load_agent_roster_config,
    load_api_keys_registry,
    load_foundry_config,
    load_models_config,
    load_project_config,
    save_agent_roster_config,
    save_api_keys_registry,
    save_models_config,
    save_project_config,
)
from foundry.config.schemas import (
    AgentRosterConfig,
    AgentSlotConfig,
    ApiKeyEntry,
    ApiKeysRegistryConfig,
    AutonomyConfig,
    DataPolicyConfig,
    FoundryGlobalConfig,
    ModelProfile,
    ModelsConfig,
    ProjectConfig,
    ProviderConfig,
    TokenPrice,
    TransportKind,
    normalize_api_key_var_name,
)
from foundry.gateway.protocol import GatewayRequest, TransportError
from foundry.gateway.router import GatewayPolicyError, ModelGateway
from foundry.gateway.transports.openai_codex_cli import detect_codex_cli
from foundry.security.vault import CredentialVault, VaultError

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


def _load_roster(config_dir: Path) -> AgentRosterConfig:
    path = config_dir / "agents.yaml"
    return load_agent_roster_config(path) if path.is_file() else default_agent_roster_config()


def _load_keys_registry(config_dir: Path) -> ApiKeysRegistryConfig:
    path = config_dir / "api_keys.yaml"
    return load_api_keys_registry(path) if path.is_file() else default_api_keys_registry()


def _open_vault(
    st: DaemonState,
    passphrase_override: str | None = None,
) -> tuple[CredentialVault, Path, str | None]:
    cfg = _load_foundry(st.config_dir)
    vault_path = Path(cfg.secrets.vault_path).expanduser()
    resolved_passphrase = (
        passphrase_override or st.vault_passphrase or os.environ.get("FOUNDRY_VAULT_PASSPHRASE")
    )
    vault = CredentialVault(
        vault_path=vault_path,
        passphrase=resolved_passphrase,
        backend_order=cfg.secrets.backend_order,
    )
    return vault, vault_path, resolved_passphrase


def _mask_secret(secret: str) -> str:
    stripped = secret.strip()
    if len(stripped) >= 10:
        return f"{stripped[:3]}••••{stripped[-4:]}"
    if len(stripped) >= 5:
        return f"••••{stripped[-2:]}"
    return "••••"


def _derive_chat_title(prompt: str, explicit_title: str | None = None) -> str:
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()[:80]
    first_line = next((line.strip() for line in prompt.splitlines() if line.strip()), "New run")
    return first_line[:56] + ("…" if len(first_line) > 56 else "")


def _provider_label(provider_id: str) -> str:
    entry = get_provider_catalog_entry(provider_id)
    return entry.label if entry is not None else provider_id


def _ensure_key_provider_in_models(
    models_cfg: ModelsConfig,
    entry: ApiKeyEntry,
) -> tuple[ModelsConfig, str]:
    """Ensure `models_cfg.providers` contains a provider entry bound to `entry`'s key_ref."""
    provider_key = f"key_{entry.name.lower()}"
    cat = get_provider_catalog_entry(entry.provider)
    if cat is not None:
        prov_cfg = provider_config_from_catalog(cat, key_ref=entry.key_ref)
    else:
        prov_cfg = ProviderConfig(
            transport=TransportKind.OPENAI_COMPAT,
            base_url="https://api.openai.com/v1",
            key_ref=entry.key_ref,
            family=entry.provider or "custom",
        )
    existing = models_cfg.providers.get(provider_key)
    if existing is not None and existing == prov_cfg:
        return models_cfg, provider_key
    updated_providers = {**models_cfg.providers, provider_key: prov_cfg}
    return models_cfg.model_copy(update={"providers": updated_providers}), provider_key


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
    # Always place workspace project from .foundry/project.yaml first
    if proj_cfg.name in grouped:
        pid_runs = grouped[proj_cfg.name]
        latest = pid_runs[0]
        seen.add(proj_cfg.name)
        summaries.append(
            ProjectSummary(
                id=proj_cfg.name,
                name=proj_cfg.name,
                stack_pack=proj_cfg.stack_pack,
                run_count=len(pid_runs),
                latest_run_id=latest.id,
                latest_phase=latest.phase,
            )
        )
    else:
        seen.add(proj_cfg.name)
        summaries.append(
            ProjectSummary(
                id=proj_cfg.name,
                name=proj_cfg.name,
                stack_pack=proj_cfg.stack_pack,
                run_count=0,
            )
        )

    for pid, pid_runs in sorted(grouped.items()):
        if pid in seen:
            continue
        seen.add(pid)
        latest = pid_runs[0]
        summaries.append(
            ProjectSummary(
                id=pid,
                name=pid,
                stack_pack=proj_cfg.stack_pack,
                run_count=len(pid_runs),
                latest_run_id=latest.id,
                latest_phase=latest.phase,
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


@api_router.post("/runs", response_model=ChatRunCreatedResponse)
async def create_run_chat(body: CreateRunRequest, request: Request) -> ChatRunCreatedResponse:
    st = _state(request)
    cleaned_prompt = body.prompt.strip()
    if not cleaned_prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    proj_cfg = _load_project(st.project_dir)
    if body.autonomy_preset is not None and body.autonomy_preset != proj_cfg.autonomy.preset:
        new_autonomy = proj_cfg.autonomy.model_copy(update={"preset": body.autonomy_preset})
        proj_cfg = proj_cfg.model_copy(update={"autonomy": new_autonomy})
        save_project_config(proj_cfg, st.project_dir / ".foundry" / "project.yaml")

    project_slug = (body.project_id or proj_cfg.name).strip() or proj_cfg.name
    budget_usd = body.budget_usd if body.budget_usd is not None else proj_cfg.budgets.per_run_usd
    title = _derive_chat_title(cleaned_prompt, body.title)

    run = await st.repo.create_run(
        project_id=project_slug,
        budget_usd=budget_usd,
        data_policy=proj_cfg.data_policy.model_dump(mode="json"),
        phase="planning",
    )

    thread_id = f"chat_{run.id}"
    await st.repo.ensure_thread(
        run_id=run.id,
        kind="human_chat",
        subject_id=title,
        thread_id=thread_id,
    )
    human_msg = await st.repo.post_message(
        thread_id=thread_id,
        from_id="human",
        to_id="orchestrator",
        body=cleaned_prompt,
    )

    roster = _load_roster(st.config_dir)
    enabled_slots = sorted(
        (slot for slot in roster.agents if slot.enabled),
        key=lambda s: int(s.layer),
    )
    slot_to_agent_id: dict[str, str] = {}
    registered_agents: list[AgentRecord] = []
    for slot in enabled_slots:
        parent_agent_id = slot_to_agent_id.get(slot.parent_id) if slot.parent_id else None
        agent_rec = await st.repo.register_agent(
            run_id=run.id,
            role=slot.role,
            domain=slot.domain,
            parent_agent_id=parent_agent_id,
            model_profile=slot.model_profile,
            graph_thread_id=f"{run.id}:layer{slot.layer}:{slot.id}",
        )
        slot_to_agent_id[slot.id] = agent_rec.id
        registered_agents.append(agent_rec)

    layer_counts = {layer: sum(1 for s in enabled_slots if s.layer == layer) for layer in (1, 2, 3)}
    bound_keys = sorted({s.api_key_var for s in enabled_slots if s.api_key_var})
    key_summary = f" using bound key variable(s) {', '.join(bound_keys)}" if bound_keys else ""
    reply_body = (
        f"Initialized run {run.id} for {project_slug} with {len(registered_agents)} active "
        f"agents across 3 layers (L1 orchestrators: {layer_counts[1]}, L2 domain leads: "
        f"{layer_counts[2]}, L3 implementers & verifiers: {layer_counts[3]}){key_summary}. "
        f"Orchestration is in planning mode — review agent hierarchy in the Agents tab or "
        f"send follow-up instructions below."
    )
    orch_msg = await st.repo.post_message(
        thread_id=thread_id,
        from_id="orchestrator",
        to_id="human",
        body=reply_body,
    )

    await st.repo.emit_event(
        run_id=run.id,
        event_type="run.created",
        subject_id=run.id,
        payload={
            "run_id": run.id,
            "project_id": project_slug,
            "title": title,
            "phase": run.phase,
            "agents_registered": len(registered_agents),
        },
    )
    await st.repo.emit_event(
        run_id=run.id,
        event_type="chat.message_posted",
        subject_id=orch_msg.id,
        payload={"thread_id": thread_id, "message_id": orch_msg.id, "from_id": "orchestrator"},
    )
    await st.relay.flush_once()

    summary = ChatSessionSummary(
        run_id=run.id,
        title=title,
        project_id=run.project_id,
        phase=run.phase,
        message_count=2,
        agent_count=len(registered_agents),
        is_chat_session=True,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )
    return ChatRunCreatedResponse(
        run=run,
        chat=summary,
        messages=[human_msg, orch_msg],
        agents=registered_agents,
    )


@api_router.get("/chats", response_model=list[ChatSessionSummary])
async def list_chats(request: Request) -> list[ChatSessionSummary]:
    st = _state(request)
    runs = await st.repo.list_runs()
    summaries: list[ChatSessionSummary] = []
    for run in runs:
        thread = await st.repo.get_thread(f"chat_{run.id}")
        msgs = await st.repo.list_messages(f"chat_{run.id}") if thread is not None else []
        agents = await st.repo.list_agents(run.id)
        is_chat = thread is not None
        subject = thread.get("subject_id") if thread is not None else None
        title = (
            str(subject)
            if isinstance(subject, str) and subject.strip()
            else f"{run.project_id} · {run.phase.replace('_', ' ')}"
        )
        summaries.append(
            ChatSessionSummary(
                run_id=run.id,
                title=title,
                project_id=run.project_id,
                phase=run.phase,
                message_count=len(msgs),
                agent_count=len(agents),
                is_chat_session=is_chat,
                started_at=run.started_at,
                ended_at=run.ended_at,
            )
        )
    return summaries


@api_router.delete("/runs/{run_id}", response_model=RunDeleteResponse)
async def delete_run(run_id: str, request: Request) -> RunDeleteResponse:
    st = _state(request)
    try:
        await st.repo.delete_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RunDeleteResponse(deleted=run_id)


@api_router.post("/chats/purge-test-runs", response_model=PurgeTestRunsResponse)
async def purge_test_runs(request: Request) -> PurgeTestRunsResponse:
    st = _state(request)
    runs = await st.repo.list_runs()
    deleted_ids: list[str] = []
    for run in runs:
        thread = await st.repo.get_thread(f"chat_{run.id}")
        if thread is None:
            await st.repo.delete_run(run.id)
            deleted_ids.append(run.id)
    return PurgeTestRunsResponse(
        deleted_count=len(deleted_ids),
        deleted_run_ids=deleted_ids,
    )


@api_router.get("/runs/{run_id}/messages", response_model=list[MessageRecord])
async def list_run_messages(run_id: str, request: Request) -> list[MessageRecord]:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await st.repo.list_messages(f"chat_{run_id}")


async def _run_orchestrator_chat_turn(
    st: DaemonState,
    run: RunRecord,
    thread_id: str,
) -> MessageRecord:
    """Invoke the Layer 1 Orchestrator (single-shot) using its configured key/subscription."""
    models_cfg = ensure_subscription_provider(_load_models(st.config_dir))
    proj_cfg = _load_project(st.project_dir)
    roster = _load_roster(st.config_dir)
    registry = _load_keys_registry(st.config_dir)
    vault, _, _ = _open_vault(st)

    orch_slot = next(
        (s for s in roster.agents if s.layer == 1 and s.enabled),
        None,
    )
    history = await st.repo.list_messages(thread_id)
    llm_messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                f"You are Foundry's Layer 1 Project Orchestrator for project '{run.project_id}' "
                f"(run {run.id}, phase={run.phase}). Respond concisely and concretely."
            ),
        }
    ]
    for item in history[-10:]:
        role = "user" if item.from_id == "human" else "assistant"
        llm_messages.append({"role": role, "content": item.body})

    provider_override: str | None = None
    model_override: str | None = None
    key_ref_override: str | None = None
    profile_label = orch_slot.model_profile if orch_slot else "@planner"

    if orch_slot is not None:
        if orch_slot.auth_source == "openai_subscription":
            provider_override = "openai_subscription_cli"
            model_override = orch_slot.model_name or "gpt-5"
        elif orch_slot.auth_source == "api_key" and orch_slot.api_key_var:
            entry = next((k for k in registry.keys if k.name == orch_slot.api_key_var), None)
            if entry is not None:
                models_cfg, provider_override = _ensure_key_provider_in_models(models_cfg, entry)
                key_ref_override = entry.key_ref
                cat = get_provider_catalog_entry(entry.provider)
                default_m = cat.models[0] if cat and cat.models else None
                model_override = orch_slot.model_name or default_m

    gateway = ModelGateway(
        models_cfg,
        vault=vault,
        data_policy=proj_cfg.data_policy,
    )
    gw_req = GatewayRequest(
        role="orchestrator",
        messages=llm_messages,
        tools=[],
        max_tokens=1024,
        key_ref_override=key_ref_override,
        provider_override=provider_override,
        model_override=model_override,
    )
    try:
        resp = await gateway.complete(gw_req)
        reply_text = resp.content.strip() or "(Empty response from model)"
        await st.repo.record_llm_call(
            run_id=run.id,
            provider=resp.provider_id,
            model=resp.model,
            profile=profile_label,
            transport=str(resp.transport),
            billing_mode=str(resp.billing_mode),
            input_tokens=resp.tokens_in,
            cached_tokens=resp.tokens_cached,
            output_tokens=resp.tokens_out,
            cost_usd=resp.cost_usd,
            est_value_usd=resp.est_value_usd,
            latency_ms=resp.latency_ms,
            replayed=resp.replayed,
            prompt_hash=resp.prompt_hash,
        )
    except (GatewayPolicyError, TransportError, VaultError, ValueError) as exc:
        reply_text = f"[Orchestrator gateway notice] {exc}"
    finally:
        await gateway.aclose()

    reply_msg = await st.repo.post_message(
        thread_id=thread_id,
        from_id="orchestrator",
        to_id="human",
        body=reply_text,
    )
    await st.repo.emit_event(
        run_id=run.id,
        event_type="chat.message_posted",
        subject_id=reply_msg.id,
        payload={
            "thread_id": thread_id,
            "message_id": reply_msg.id,
            "from_id": "orchestrator",
            "body": reply_msg.body,
        },
    )
    return reply_msg


@api_router.post("/runs/{run_id}/messages", response_model=MessageRecord)
async def create_run_message(
    run_id: str,
    body: ChatMessageCreateRequest,
    request: Request,
) -> MessageRecord:
    st = _state(request)
    try:
        run = await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    cleaned = body.body.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Message body cannot be empty")

    thread_id = f"chat_{run_id}"
    await st.repo.ensure_thread(
        run_id=run_id,
        kind="human_chat",
        subject_id=_derive_chat_title(cleaned),
        thread_id=thread_id,
    )
    msg = await st.repo.post_message(
        thread_id=thread_id,
        from_id=body.from_id,
        to_id="orchestrator" if body.from_id == "human" else "human",
        body=cleaned,
    )
    await st.repo.emit_event(
        run_id=run.id,
        event_type="chat.message_posted",
        subject_id=msg.id,
        payload={
            "thread_id": thread_id,
            "message_id": msg.id,
            "from_id": msg.from_id,
            "body": msg.body,
        },
    )
    if body.invoke_orchestrator and body.from_id == "human":
        await _run_orchestrator_chat_turn(st, run, thread_id)
    await st.relay.flush_once()
    return msg


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


@api_router.post("/runs/{run_id}/agents", response_model=AgentRecord)
async def add_run_agent(
    run_id: str,
    body: RunAgentCreateRequest,
    request: Request,
) -> AgentRecord:
    st = _state(request)
    try:
        await st.repo.get_run(run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    norm_profile = f"@{body.model_profile.strip().lstrip('@')}"
    models_cfg = _load_models(st.config_dir)
    if norm_profile not in models_cfg.profiles:
        raise HTTPException(status_code=400, detail=f"Unknown model profile: {norm_profile}")

    agent = await st.repo.register_agent(
        run_id=run_id,
        role=body.role,
        domain=body.domain.strip() if body.domain and body.domain.strip() else None,
        parent_agent_id=body.parent_agent_id,
        model_profile=norm_profile,
        graph_thread_id=f"{run_id}:agent:{str(ULID()).lower()[:8]}",
    )
    await st.repo.emit_event(
        run_id=run_id,
        event_type="agent.registered",
        subject_id=agent.id,
        payload={"role": agent.role, "domain": agent.domain, "model_profile": agent.model_profile},
    )
    await st.relay.flush_once()
    return agent


@api_router.delete("/runs/{run_id}/agents/{agent_id}", response_model=AgentRecord)
async def remove_run_agent(run_id: str, agent_id: str, request: Request) -> AgentRecord:
    st = _state(request)
    try:
        agent = await st.repo.get_agent(agent_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if agent.run_id != run_id:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} does not belong to {run_id}")
    updated = await st.repo.update_agent_status(agent_id, "removed")
    await st.repo.emit_event(
        run_id=run_id,
        event_type="agent.removed",
        subject_id=agent_id,
        payload={"agent_id": agent_id, "role": updated.role},
    )
    await st.relay.flush_once()
    return updated


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


@api_router.get("/settings/catalog", response_model=ProviderCatalogResponse)
async def get_settings_catalog() -> ProviderCatalogResponse:
    return ProviderCatalogResponse(providers=list_provider_catalog())


@api_router.get("/settings/providers", response_model=dict[str, ProviderConfig])
async def get_settings_providers(request: Request) -> dict[str, ProviderConfig]:
    return _load_models(_state(request).config_dir).providers


@api_router.get("/settings/models", response_model=ModelsConfig)
async def get_settings_models(request: Request) -> ModelsConfig:
    return ensure_subscription_provider(_load_models(_state(request).config_dir))


@api_router.put("/settings/models/assignments", response_model=ModelsConfig)
async def update_model_assignments(
    body: ModelAssignmentsUpdate,
    request: Request,
) -> ModelsConfig:
    st = _state(request)
    cfg = ensure_subscription_provider(_load_models(st.config_dir))
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


@api_router.put("/settings/models/profiles", response_model=ModelsConfig)
async def upsert_model_profile(
    body: ModelProfileUpsertRequest,
    request: Request,
) -> ModelsConfig:
    st = _state(request)
    cfg = ensure_subscription_provider(_load_models(st.config_dir))
    norm_profile = f"@{body.profile_name.strip().lstrip('@')}"
    if not norm_profile or len(norm_profile) < 2:
        raise HTTPException(status_code=400, detail="Profile name must not be empty")
    model_id = body.model.strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="Model name cannot be empty")

    target_provider_id: str
    resolved_family: str | None = None

    if body.auth_source == "openai_subscription":
        target_provider_id = "openai_subscription_cli"
        resolved_family = "openai"
        if not is_model_compatible_with_provider("openai_codex_cli", model_id):
            raise HTTPException(
                status_code=400,
                detail=f"Model '{model_id}' is not supported by OpenAI Subscription (Codex CLI).",
            )
    elif body.auth_source == "api_key":
        if not body.api_key_var:
            raise HTTPException(status_code=400, detail="api_key_var is required for api_key mode")
        try:
            norm_key = normalize_api_key_var_name(body.api_key_var)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        registry = _load_keys_registry(st.config_dir)
        key_entry = next((k for k in registry.keys if k.name == norm_key), None)
        if key_entry is None:
            raise HTTPException(status_code=400, detail=f"Unknown API key variable '{norm_key}'")
        if not is_model_compatible_with_provider(key_entry.provider, model_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Model '{model_id}' is not compatible with "
                    f"{_provider_label(key_entry.provider)} key '{key_entry.name}'."
                ),
            )
        cfg, target_provider_id = _ensure_key_provider_in_models(cfg, key_entry)
        resolved_family = cfg.providers[target_provider_id].family
    else:
        raw_prov = (body.provider or "").strip()
        if raw_prov in cfg.providers:
            target_provider_id = raw_prov
            cat = get_provider_catalog_entry(raw_prov)
            check_id = cat.id if cat else raw_prov
            if not is_model_compatible_with_provider(check_id, model_id):
                raise HTTPException(
                    status_code=400,
                    detail=f"Model '{model_id}' is not compatible with provider '{raw_prov}'.",
                )
            resolved_family = cfg.providers[target_provider_id].family
        else:
            cat = get_provider_catalog_entry(raw_prov) if raw_prov else None
            if cat is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown provider '{raw_prov}'",
                )
            if not is_model_compatible_with_provider(cat.id, model_id):
                raise HTTPException(
                    status_code=400,
                    detail=f"Model '{model_id}' is not compatible with provider '{cat.label}'.",
                )
            target_provider_id = cat.default_provider_id
            if target_provider_id not in cfg.providers:
                cfg = cfg.model_copy(
                    update={
                        "providers": {
                            **cfg.providers,
                            target_provider_id: provider_config_from_catalog(cat),
                        }
                    }
                )
            resolved_family = cat.family

    new_profile = ModelProfile(
        provider=target_provider_id,
        model=model_id,
        family=resolved_family,
        price=TokenPrice(
            **{
                "in": body.input_per_m,
                "cached_in": body.cached_input_per_m,
                "out": body.output_per_m,
            }
        ),
        fallbacks=[f"@{fb.strip().lstrip('@')}" for fb in body.fallbacks if fb.strip()],
    )
    updated_profiles = {**cfg.profiles, norm_profile: new_profile}
    try:
        updated = ModelsConfig(
            providers=cfg.providers,
            profiles=updated_profiles,
            assignments=cfg.assignments,
            policies=cfg.policies,
        )
        save_models_config(updated, st.config_dir / "models.yaml")
    except (ValueError, ConfigError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return updated


@api_router.delete("/settings/models/profiles/{profile_name}", response_model=ModelsConfig)
async def delete_model_profile(profile_name: str, request: Request) -> ModelsConfig:
    st = _state(request)
    cfg = ensure_subscription_provider(_load_models(st.config_dir))
    norm = f"@{profile_name.strip().lstrip('@')}"
    if norm not in cfg.profiles:
        raise HTTPException(status_code=404, detail=f"Model profile not found: {norm}")
    assigned_roles = [r for r, p in cfg.assignments.roles.items() if p == norm]
    if assigned_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete {norm}: still assigned to role(s) {', '.join(assigned_roles)}.",
        )
    if norm in {cfg.assignments.project_default, cfg.assignments.global_default}:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete {norm}: configured as project/global default.",
        )
    remaining_profiles = {}
    for k, prof in cfg.profiles.items():
        if k == norm:
            continue
        cleaned_fbs = [fb for fb in prof.fallbacks if fb != norm]
        remaining_profiles[k] = prof.model_copy(update={"fallbacks": cleaned_fbs})
    updated = ModelsConfig(
        providers=cfg.providers,
        profiles=remaining_profiles,
        assignments=cfg.assignments,
        policies=cfg.policies,
    )
    save_models_config(updated, st.config_dir / "models.yaml")
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


def _build_keys_status(st: DaemonState) -> ApiKeysStatusResponse:
    vault, vault_path, resolved_passphrase = _open_vault(st)
    backend = vault.active_backend
    vault_unlocked = backend == "keyring" or bool(resolved_passphrase)
    registry = _load_keys_registry(st.config_dir)
    roster = _load_roster(st.config_dir)
    assigned_map: dict[str, list[str]] = defaultdict(list)
    for slot in roster.agents:
        if slot.api_key_var:
            assigned_map[slot.api_key_var].append(slot.name)

    keys = [
        ApiKeyPublicRecord(
            name=entry.name,
            provider=entry.provider,
            provider_label=_provider_label(entry.provider),
            key_ref=entry.key_ref,
            masked_preview=entry.masked_preview,
            backend=entry.backend,
            assigned_agents=assigned_map.get(entry.name, []),
            created_at=entry.created_at,
        )
        for entry in registry.keys
    ]
    return ApiKeysStatusResponse(
        backend=backend,
        vault_unlocked=vault_unlocked,
        vault_exists=vault_path.exists(),
        keys=keys,
    )


@api_router.get("/settings/keys", response_model=ApiKeysStatusResponse)
async def get_settings_keys(request: Request) -> ApiKeysStatusResponse:
    return _build_keys_status(_state(request))


@api_router.post("/settings/keys/{key_name}/models", response_model=KeyModelsResponse)
async def discover_settings_key_models(key_name: str, request: Request) -> KeyModelsResponse:
    st = _state(request)
    try:
        norm_name = normalize_api_key_var_name(key_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registry = _load_keys_registry(st.config_dir)
    target = next((item for item in registry.keys if item.name == norm_name), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"API key variable not found: {norm_name}")

    secret: str | None = None
    vault, _, _ = _open_vault(st)
    try:
        secret = vault.get_secret(target.key_ref, allow_env_fallback=True)
    except VaultError:
        secret = None

    models_list, source, detail = await discover_provider_models(target.provider, secret)
    return KeyModelsResponse(
        key_name=target.name,
        provider=target.provider,
        provider_label=_provider_label(target.provider),
        models=models_list,
        source=source,
        detail=detail,
    )


async def _build_openai_subscription_status(st: DaemonState) -> OpenAISubscriptionStatusResponse:
    models_cfg = ensure_subscription_provider(_load_models(st.config_dir))
    installed, cli_path, cli_version = await detect_codex_cli()
    cat = get_provider_catalog_entry("openai_codex_cli")
    return OpenAISubscriptionStatusResponse(
        enabled=bool(models_cfg.policies.allow_openai_subscription_cli),
        cli_installed=installed,
        cli_path=cli_path,
        cli_version=cli_version,
        models=list(cat.models) if cat else ["gpt-5", "gpt-5-codex", "o4-mini", "gpt-4.1"],
        supports_tool_calling=False,
        policy_note=(
            "Single-shot non-tool execution via your logged-in OpenAI Codex CLI (`codex exec`). "
            "Never reads `~/.codex` OAuth tokens and blocks multi-step tool loops (A7-2)."
        ),
        setup_hint="Install with `npm i -g @openai/codex` and sign in via `codex login`.",
    )


@api_router.get("/settings/subscriptions/openai", response_model=OpenAISubscriptionStatusResponse)
async def get_openai_subscription_status(request: Request) -> OpenAISubscriptionStatusResponse:
    return await _build_openai_subscription_status(_state(request))


@api_router.put("/settings/subscriptions/openai", response_model=OpenAISubscriptionStatusResponse)
async def update_openai_subscription_status(
    body: OpenAISubscriptionUpdateRequest,
    request: Request,
) -> OpenAISubscriptionStatusResponse:
    st = _state(request)
    cfg = ensure_subscription_provider(_load_models(st.config_dir))
    updated_policies = cfg.policies.model_copy(
        update={"allow_openai_subscription_cli": body.enabled}
    )
    updated = cfg.model_copy(update={"policies": updated_policies})
    save_models_config(updated, st.config_dir / "models.yaml")
    return await _build_openai_subscription_status(st)


@api_router.post("/settings/test-connection", response_model=TestConnectionResponse)
async def test_settings_connection(
    body: TestConnectionRequest,
    request: Request,
) -> TestConnectionResponse:
    st = _state(request)
    models_cfg = ensure_subscription_provider(_load_models(st.config_dir))
    proj_cfg = _load_project(st.project_dir)
    vault, _, _ = _open_vault(st)
    model_id = body.model.strip()

    if body.auth_source == "openai_subscription":
        if not is_model_compatible_with_provider("openai_codex_cli", model_id):
            raise HTTPException(
                status_code=400,
                detail=f"Model '{model_id}' is not supported by OpenAI Subscription (Codex CLI).",
            )
        provider_override = "openai_subscription_cli"
        key_ref_override = None
        label = "OpenAI Subscription (Codex CLI)"
        transport_label = str(TransportKind.OPENAI_CODEX_CLI)
    else:
        if not body.api_key_var:
            raise HTTPException(status_code=400, detail="api_key_var is required for api_key mode")
        try:
            norm_key = normalize_api_key_var_name(body.api_key_var)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        registry = _load_keys_registry(st.config_dir)
        key_entry = next((k for k in registry.keys if k.name == norm_key), None)
        if key_entry is None:
            raise HTTPException(status_code=404, detail=f"API key variable not found: {norm_key}")
        if not is_model_compatible_with_provider(key_entry.provider, model_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Model '{model_id}' is not compatible with "
                    f"{_provider_label(key_entry.provider)} key '{key_entry.name}'."
                ),
            )
        models_cfg, provider_override = _ensure_key_provider_in_models(models_cfg, key_entry)
        key_ref_override = key_entry.key_ref
        label = _provider_label(key_entry.provider)
        transport_label = str(models_cfg.providers[provider_override].transport)

    gateway = ModelGateway(
        models_cfg,
        vault=vault,
        data_policy=proj_cfg.data_policy,
    )
    gw_req = GatewayRequest(
        role="orchestrator",
        messages=[{"role": "user", "content": body.prompt}],
        tools=[],
        max_tokens=128,
        key_ref_override=key_ref_override,
        provider_override=provider_override,
        model_override=model_id,
    )
    try:
        resp = await gateway.complete(gw_req)
        return TestConnectionResponse(
            ok=True,
            provider=label,
            model=resp.model,
            transport=str(resp.transport),
            latency_ms=resp.latency_ms,
            reply_preview=resp.content.strip()[:240],
        )
    except (GatewayPolicyError, TransportError, VaultError) as exc:
        return TestConnectionResponse(
            ok=False,
            provider=label,
            model=model_id,
            transport=transport_label,
            error=str(exc),
        )
    finally:
        await gateway.aclose()


@api_router.post("/settings/vault/unlock", response_model=ApiKeysStatusResponse)
async def unlock_settings_vault(
    body: VaultUnlockRequest,
    request: Request,
) -> ApiKeysStatusResponse:
    st = _state(request)
    vault, vault_path, _ = _open_vault(st, body.passphrase)
    if vault.active_backend == "encrypted_vault" and vault_path.exists():
        try:
            _ = vault.list_keys()
        except VaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    st.vault_passphrase = body.passphrase
    return _build_keys_status(st)


@api_router.post("/settings/keys", response_model=ApiKeyPublicRecord)
async def upsert_settings_key(
    body: ApiKeyCreateRequest,
    request: Request,
) -> ApiKeyPublicRecord:
    st = _state(request)
    try:
        norm_name = normalize_api_key_var_name(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cleaned_secret = body.secret.strip()
    if not cleaned_secret:
        raise HTTPException(status_code=400, detail="API key secret cannot be empty")

    vault, _, _ = _open_vault(st, body.passphrase)
    key_ref = f"vault:var_{norm_name.lower()}"
    try:
        backend = vault.set_secret(key_ref, cleaned_secret)
    except VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.passphrase:
        st.vault_passphrase = body.passphrase

    raw_provider = body.provider.strip().lower() or "custom"
    cat = get_provider_catalog_entry(raw_provider)
    norm_provider = cat.id if cat is not None else raw_provider

    registry = _load_keys_registry(st.config_dir)
    entry = ApiKeyEntry(
        name=norm_name,
        provider=norm_provider,
        key_ref=key_ref,
        masked_preview=_mask_secret(cleaned_secret),
        backend=backend,
    )
    remaining = [item for item in registry.keys if item.name != norm_name]
    remaining.append(entry)
    remaining.sort(key=lambda item: item.name)
    save_api_keys_registry(
        ApiKeysRegistryConfig(keys=remaining),
        st.config_dir / "api_keys.yaml",
    )

    roster = _load_roster(st.config_dir)
    assigned = [slot.name for slot in roster.agents if slot.api_key_var == norm_name]
    return ApiKeyPublicRecord(
        name=entry.name,
        provider=entry.provider,
        provider_label=_provider_label(entry.provider),
        key_ref=entry.key_ref,
        masked_preview=entry.masked_preview,
        backend=entry.backend,
        assigned_agents=assigned,
        created_at=entry.created_at,
    )


@api_router.delete("/settings/keys/{key_name}")
async def delete_settings_key(key_name: str, request: Request) -> dict[str, str]:
    st = _state(request)
    try:
        norm_name = normalize_api_key_var_name(key_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registry = _load_keys_registry(st.config_dir)
    target = next((item for item in registry.keys if item.name == norm_name), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"API key variable not found: {norm_name}")

    vault, _, _ = _open_vault(st)
    try:
        vault.delete_secret(target.key_ref)
    except VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_api_keys_registry(
        ApiKeysRegistryConfig(keys=[item for item in registry.keys if item.name != norm_name]),
        st.config_dir / "api_keys.yaml",
    )

    roster = _load_roster(st.config_dir)
    updated_agents = [
        slot.model_copy(
            update={"auth_source": "profile_default", "api_key_var": None, "model_name": None}
        )
        if slot.api_key_var == norm_name
        else slot
        for slot in roster.agents
    ]
    save_agent_roster_config(
        AgentRosterConfig(agents=updated_agents),
        st.config_dir / "agents.yaml",
    )
    return {"deleted": norm_name}


def _validate_slot_refs(
    st: DaemonState,
    slot: AgentSlotConfig,
    existing_slots: list[AgentSlotConfig],
) -> None:
    models_cfg = ensure_subscription_provider(_load_models(st.config_dir))
    if slot.model_profile not in models_cfg.profiles:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model profile '{slot.model_profile}'",
        )
    if slot.auth_source == "api_key":
        if not slot.api_key_var:
            raise HTTPException(
                status_code=400,
                detail="api_key_var is required when auth_source is 'api_key'",
            )
        registry = _load_keys_registry(st.config_dir)
        key_entry = next((k for k in registry.keys if k.name == slot.api_key_var), None)
        if key_entry is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown API key variable '{slot.api_key_var}'",
            )
        if slot.model_name and not is_model_compatible_with_provider(
            key_entry.provider, slot.model_name
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Model '{slot.model_name}' is not compatible with "
                    f"{_provider_label(key_entry.provider)} key '{key_entry.name}'."
                ),
            )
    elif (
        slot.auth_source == "openai_subscription"
        and slot.model_name
        and not is_model_compatible_with_provider("openai_codex_cli", slot.model_name)
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{slot.model_name}' is not supported by OpenAI Subscription (Codex CLI)."
            ),
        )
    if slot.parent_id is not None:
        if slot.parent_id == slot.id:
            raise HTTPException(status_code=400, detail="Agent cannot be its own parent")
        parent = next((item for item in existing_slots if item.id == slot.parent_id), None)
        if parent is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown parent agent '{slot.parent_id}'",
            )
        if int(parent.layer) != int(slot.layer) - 1:
            raise HTTPException(
                status_code=400,
                detail=f"Layer {slot.layer} agent must have a Layer {int(slot.layer) - 1} parent",
            )


@api_router.get("/settings/agents", response_model=AgentRosterConfig)
async def get_settings_agents(request: Request) -> AgentRosterConfig:
    return _load_roster(_state(request).config_dir)


@api_router.post("/settings/agents", response_model=AgentRosterConfig)
async def create_settings_agent(
    body: AgentSlotCreateRequest,
    request: Request,
) -> AgentRosterConfig:
    st = _state(request)
    roster = _load_roster(st.config_dir)
    slot_id = f"l{body.layer}_{str(ULID()).lower()[:8]}"
    try:
        slot = AgentSlotConfig(
            id=slot_id,
            name=body.name.strip(),
            layer=body.layer,
            role=body.role,
            domain=body.domain,
            parent_id=body.parent_id,
            model_profile=body.model_profile,
            auth_source=body.auth_source,
            api_key_var=body.api_key_var,
            model_name=body.model_name,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _validate_slot_refs(st, slot, roster.agents)
    updated = AgentRosterConfig(agents=[*roster.agents, slot])
    save_agent_roster_config(updated, st.config_dir / "agents.yaml")
    return updated


@api_router.put("/settings/agents/{agent_id}", response_model=AgentRosterConfig)
async def update_settings_agent(
    agent_id: str,
    body: AgentSlotUpdateRequest,
    request: Request,
) -> AgentRosterConfig:
    st = _state(request)
    roster = _load_roster(st.config_dir)
    current = next((item for item in roster.agents if item.id == agent_id), None)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Agent slot not found: {agent_id}")

    patch: dict[str, Any] = {}
    if body.name is not None:
        patch["name"] = body.name.strip()
    if body.layer is not None:
        patch["layer"] = body.layer
    if body.role is not None:
        patch["role"] = body.role
    if body.clear_domain:
        patch["domain"] = None
    elif body.domain is not None:
        patch["domain"] = body.domain
    if body.clear_parent:
        patch["parent_id"] = None
    elif body.parent_id is not None:
        patch["parent_id"] = body.parent_id
    if body.model_profile is not None:
        patch["model_profile"] = body.model_profile
    if body.auth_source is not None:
        patch["auth_source"] = body.auth_source
    if body.clear_api_key:
        patch["api_key_var"] = None
        if body.auth_source is None and current.auth_source == "api_key":
            patch["auth_source"] = "profile_default"
    elif body.api_key_var is not None:
        patch["api_key_var"] = body.api_key_var
        if body.auth_source is None:
            patch["auth_source"] = "api_key"
    if body.clear_model_name:
        patch["model_name"] = None
    elif body.model_name is not None:
        patch["model_name"] = body.model_name
    if body.enabled is not None:
        patch["enabled"] = body.enabled

    try:
        updated_slot = AgentSlotConfig.model_validate({**current.model_dump(), **patch})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _validate_slot_refs(st, updated_slot, roster.agents)
    new_list = [updated_slot if item.id == agent_id else item for item in roster.agents]
    updated = AgentRosterConfig(agents=new_list)
    save_agent_roster_config(updated, st.config_dir / "agents.yaml")
    return updated


@api_router.delete("/settings/agents/{agent_id}", response_model=AgentRosterConfig)
async def delete_settings_agent(agent_id: str, request: Request) -> AgentRosterConfig:
    st = _state(request)
    roster = _load_roster(st.config_dir)
    if not any(item.id == agent_id for item in roster.agents):
        raise HTTPException(status_code=404, detail=f"Agent slot not found: {agent_id}")

    remaining: list[AgentSlotConfig] = []
    for item in roster.agents:
        if item.id == agent_id:
            continue
        if item.parent_id == agent_id:
            remaining.append(item.model_copy(update={"parent_id": None}))
        else:
            remaining.append(item)

    updated = AgentRosterConfig(agents=remaining)
    save_agent_roster_config(updated, st.config_dir / "agents.yaml")
    return updated


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
