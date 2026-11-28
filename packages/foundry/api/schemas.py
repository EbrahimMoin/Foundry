"""Pydantic v2 request and response schemas for the Foundry Daemon API (Section 21.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field

from foundry.blackboard.models import (
    AgentRecord,
    ContractVersionRecord,
    LlmCallRecord,
    MessageRecord,
    RunRecord,
    TaskRecord,
    WriteReservationRecord,
)
from foundry.config.catalog import ProviderCatalogEntry
from foundry.config.schemas import (
    AgentAuthSource,
    AgentLayer,
    AgentRole,
    AutonomyOverrides,
    AutonomyPreset,
    EmbeddingsConfig,
    GlobalBudgetConfig,
    ObservabilityConfig,
    RuntimeConfig,
    ServerConfig,
)


class ProjectSummary(BaseModel):
    """Project descriptor aggregated from `.foundry/project.yaml` and Blackboard runs."""

    id: str
    name: str
    stack_pack: str
    run_count: int = 0
    latest_run_id: str | None = None
    latest_phase: str | None = None


class RunControlResponse(BaseModel):
    """Outcome of a run lifecycle control action (pause/resume/stop)."""

    run_id: str
    phase: str
    ended_at: datetime | None = None


class TaskDetailResponse(BaseModel):
    """Detailed view of a Blackboard task with write sets, dependencies, and active locks."""

    task: TaskRecord
    write_set: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    reservations: list[WriteReservationRecord] = Field(default_factory=list)


class ContractVersionsResponse(BaseModel):
    """Contract record paired with its immutable version history."""

    contract_id: str
    name: str
    domain: str
    kind: str
    status: str
    current_version: int
    versions: list[ContractVersionRecord] = Field(default_factory=list)


class DecisionResolveRequest(BaseModel):
    """Payload to resolve a pending Blackboard decision via Dashboard or API."""

    action: Literal["approve", "reject"] = "approve"
    choice: Any | None = None
    answer: Any | None = None
    feedback: str | None = None
    decided_by: str = "human"


class ModelCostBucket(BaseModel):
    """Aggregated spend and token telemetry for a single model or profile."""

    key: str
    calls: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost_usd: float
    est_value_usd: float


class CostsSummaryResponse(BaseModel):
    """Cost and prefix-cache telemetry summary for a run (`GET /api/runs/{id}/costs`)."""

    run_id: str
    budget_usd: float
    spent_usd: float
    total_call_cost_usd: float
    total_est_value_usd: float
    call_count: int
    cache_hit_rate: float
    by_model: list[ModelCostBucket] = Field(default_factory=list)
    by_profile: list[ModelCostBucket] = Field(default_factory=list)
    calls: list[LlmCallRecord] = Field(default_factory=list)


class FeatureMetricsSummary(BaseModel):
    """Per-feature verification and dispute telemetry."""

    name: str
    status: str
    budget_blackbox_tests_usd: float
    spent_blackbox_tests_usd: float
    dispute_rounds: int


class MetricsSummaryResponse(BaseModel):
    """Operational metrics summary for a run (`GET /api/runs/{id}/metrics`)."""

    run_id: str
    phase: str
    tasks_total: int
    tasks_by_status: dict[str, int] = Field(default_factory=dict)
    total_attempts: int = 0
    total_contract_reopens: int = 0
    active_reservations: int = 0
    contracts_count: int = 0
    pending_decisions: int = 0
    dispute_rounds_total: int = 0
    features: list[FeatureMetricsSummary] = Field(default_factory=list)
    events_count: int = 0


class ModelAssignmentsUpdate(BaseModel):
    """Update role -> model profile assignments in `models.yaml`."""

    roles: dict[str, str]


class AutonomyUpdate(BaseModel):
    """Update project autonomy preset, category overrides, or confidence threshold."""

    preset: AutonomyPreset | None = None
    overrides: AutonomyOverrides | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class DataPolicyUpdate(BaseModel):
    """Update project privacy/data policy switches."""

    allow_free_tier: bool | None = None
    allow_trace_export: bool | None = None


class NoteCreateRequest(BaseModel):
    """Persist an operator note or gotcha into the run Blackboard (`notes` table)."""

    body: str = Field(min_length=1, max_length=8000)
    kind: Literal["note", "gotcha"] = "note"
    task_id: str | None = None
    feature: str | None = None


class RuntimeSettingsResponse(BaseModel):
    """Sanitized read-only summary of `foundry.yaml` daemon & sandbox invariants."""

    server: ServerConfig
    runtime: RuntimeConfig
    embeddings: EmbeddingsConfig
    observability: ObservabilityConfig
    budgets: GlobalBudgetConfig
    secrets_backends: list[Literal["keyring", "encrypted_vault"]] = Field(default_factory=list)


class PreviewUrlResponse(BaseModel):
    """Resolved preview URL for the Dashboard iframe (`GET /api/preview/{run_id}/url`)."""

    run_id: str
    env: Literal["local", "staging", "production"]
    url: str | None = None
    available: bool = False


class CreateRunRequest(BaseModel):
    """Create a fresh chat-first Foundry run (`POST /api/runs`)."""

    prompt: str = Field(min_length=1, max_length=16000)
    title: str | None = None
    project_id: str | None = None
    autonomy_preset: AutonomyPreset | None = None
    budget_usd: float | None = Field(default=None, ge=0.0)


class ChatSessionSummary(BaseModel):
    """Sidebar chat / run history summary (`GET /api/chats`)."""

    run_id: str
    title: str
    project_id: str
    phase: str
    message_count: int = 0
    agent_count: int = 0
    is_chat_session: bool = False
    started_at: datetime
    ended_at: datetime | None = None


class ChatRunCreatedResponse(BaseModel):
    """Response after launching a new chat session (`POST /api/runs`)."""

    run: RunRecord
    chat: ChatSessionSummary
    messages: list[MessageRecord] = Field(default_factory=list)
    agents: list[AgentRecord] = Field(default_factory=list)


class ChatMessageCreateRequest(BaseModel):
    """Append a human or orchestrator message to the run's `human_chat` thread."""

    body: str = Field(min_length=1, max_length=16000)
    from_id: Literal["human", "orchestrator"] = "human"
    invoke_orchestrator: bool = False


class RunDeleteResponse(BaseModel):
    """Result after permanently deleting a single run and all child Blackboard state."""

    deleted: str


class PurgeTestRunsResponse(BaseModel):
    """Result after purging legacy/non-chat test runs from the Blackboard."""

    deleted_count: int
    deleted_run_ids: list[str] = Field(default_factory=list)


class VaultUnlockRequest(BaseModel):
    """Unlock the local `encrypted_vault` in daemon RAM for the current session."""

    passphrase: str = Field(min_length=1, max_length=512)


class ApiKeyCreateRequest(BaseModel):
    """Create or update a named API key variable (e.g., `API_KEY_1`) in CredentialVault."""

    name: str = Field(min_length=1, max_length=64)
    secret: str = Field(min_length=1, max_length=4096)
    provider: str = Field(
        default="custom",
        validation_alias=AliasChoices("provider", "provider_hint"),
    )
    passphrase: str | None = None

    model_config = {"populate_by_name": True}


class ApiKeyPublicRecord(BaseModel):
    """Sanitized public view of a stored API key variable (never contains plaintext secrets)."""

    name: str
    provider: str
    provider_label: str
    key_ref: str
    masked_preview: str
    backend: Literal["keyring", "encrypted_vault"]
    assigned_agents: list[str] = Field(default_factory=list)
    created_at: datetime


class ApiKeysStatusResponse(BaseModel):
    """Vault status and masked list of configured named API key variables."""

    backend: Literal["keyring", "encrypted_vault"]
    vault_unlocked: bool
    vault_exists: bool
    keys: list[ApiKeyPublicRecord] = Field(default_factory=list)


class ProviderCatalogResponse(BaseModel):
    """Supported official providers and curated provider-scoped models."""

    providers: list[ProviderCatalogEntry] = Field(default_factory=list)


class KeyModelsResponse(BaseModel):
    """Available models discovered or catalogued for a specific saved API key variable."""

    key_name: str
    provider: str
    provider_label: str
    models: list[str] = Field(default_factory=list)
    source: Literal["live_api", "catalog_fallback"]
    detail: str | None = None


class ModelProfileUpsertRequest(BaseModel):
    """Create or edit a model profile (`models.yaml`) bound to a key, subscription, or provider."""

    profile_name: str = Field(min_length=1, max_length=64)
    auth_source: Literal["provider_default", "api_key", "openai_subscription"] = "provider_default"
    api_key_var: str | None = None
    provider: str | None = None
    model: str = Field(min_length=1, max_length=128)
    input_per_m: float = Field(default=0.0, ge=0.0)
    cached_input_per_m: float = Field(default=0.0, ge=0.0)
    output_per_m: float = Field(default=0.0, ge=0.0)
    fallbacks: list[str] = Field(default_factory=list)


class OpenAISubscriptionStatusResponse(BaseModel):
    """Status and A7-2 compliance boundary of opt-in single-shot Codex CLI subscription mode."""

    enabled: bool
    cli_installed: bool
    cli_path: str | None = None
    cli_version: str | None = None
    models: list[str] = Field(default_factory=list)
    supports_tool_calling: bool = False
    policy_note: str
    setup_hint: str


class OpenAISubscriptionUpdateRequest(BaseModel):
    """Enable or disable opt-in single-shot OpenAI Codex CLI subscription transport."""

    enabled: bool


class TestConnectionRequest(BaseModel):
    """Test a saved API key or OpenAI Subscription CLI with a single-shot prompt."""

    auth_source: Literal["api_key", "openai_subscription"]
    api_key_var: str | None = None
    model: str = Field(min_length=1, max_length=128)
    prompt: str = Field(default="Reply with OK.", min_length=1, max_length=2000)


class TestConnectionResponse(BaseModel):
    """Structured result of testing a single-shot provider/subscription call."""

    ok: bool
    provider: str
    model: str
    transport: str
    latency_ms: int = 0
    reply_preview: str = ""
    error: str | None = None


class AgentSlotCreateRequest(BaseModel):
    """Add an agent slot into Layer 1 (Orchestrator), 2 (Lead), or 3 (Implementer/Verifier)."""

    name: str = Field(min_length=1, max_length=80)
    layer: AgentLayer
    role: AgentRole
    domain: str | None = None
    parent_id: str | None = None
    model_profile: str = "@coder"
    auth_source: AgentAuthSource = "profile_default"
    api_key_var: str | None = None
    model_name: str | None = None
    enabled: bool = True


class AgentSlotUpdateRequest(BaseModel):
    """Update an existing 3-layer agent slot."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    layer: AgentLayer | None = None
    role: AgentRole | None = None
    domain: str | None = None
    parent_id: str | None = None
    model_profile: str | None = None
    auth_source: AgentAuthSource | None = None
    api_key_var: str | None = None
    model_name: str | None = None
    enabled: bool | None = None
    clear_api_key: bool = False
    clear_model_name: bool = False
    clear_parent: bool = False
    clear_domain: bool = False


class RunAgentCreateRequest(BaseModel):
    """Register a new agent directly onto an active run."""

    role: AgentRole
    domain: str | None = None
    parent_agent_id: str | None = None
    model_profile: str = "@coder"
