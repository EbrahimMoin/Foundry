"""Pydantic v2 schemas for foundry.yaml, models.yaml, and project.yaml (v1.4)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TransportKind(StrEnum):
    """Supported first-party official SDK transports (zero LiteLLM)."""

    OPENAI_COMPAT = "openai_compat"
    GOOGLE_GENAI = "google_genai"
    ANTHROPIC = "anthropic"


class BillingMode(StrEnum):
    PAID_API = "paid_api"
    FREE_TIER = "free_tier"
    LOCAL = "local"


class DataTier(StrEnum):
    PAID = "paid"
    FREE_TRAINING_ALLOWED = "free_training_allowed"
    LOCAL = "local"


class DecisionMode(StrEnum):
    """Lean v1 binary decision modes (Notify+Undo deferred to Post-MIV)."""

    AUTO = "auto"
    APPROVE = "approve"
    HUMAN = "human"


class AutonomyPreset(StrEnum):
    AUTOPILOT = "autopilot"
    BALANCED = "balanced"
    ARCHITECT = "architect"
    FULL_CONTROL = "full_control"
    BENCHMARK = "benchmark"


class BillingEnforcement(StrEnum):
    ACTUAL_USD = "actual_usd"
    SHADOW_USD = "shadow_usd"


class ProviderConfig(BaseModel):
    """A configured official API provider or local OpenAI-compatible server."""

    transport: TransportKind
    billing_mode: BillingMode = BillingMode.PAID_API
    base_url: str | None = None
    key_ref: str | None = None
    family: str | None = None
    data_tier: DataTier = DataTier.PAID
    rpm_limit: int | None = Field(default=None, ge=1)
    cooldown_seconds: int = Field(default=60, ge=1)
    supports_tool_calling: bool = True
    max_context_tokens: int = Field(default=128_000, ge=4096)

    @model_validator(mode="after")
    def _validate_provider(self) -> ProviderConfig:
        if (
            self.transport == TransportKind.OPENAI_COMPAT
            and self.billing_mode == BillingMode.LOCAL
            and not self.base_url
        ):
            raise ValueError("Local openai_compat providers must specify base_url")
        return self


class TokenPrice(BaseModel):
    """USD per 1M tokens."""

    input_per_m: float = Field(alias="in", ge=0.0)
    cached_input_per_m: float = Field(default=0.0, alias="cached_in", ge=0.0)
    output_per_m: float = Field(alias="out", ge=0.0)

    model_config = {"populate_by_name": True}


class ModelProfile(BaseModel):
    """Named model profile referencing a provider, model ID, pricing, and fallbacks."""

    provider: str
    model: str
    price: TokenPrice = Field(default_factory=lambda: TokenPrice(**{"in": 0.0, "out": 0.0}))
    fallbacks: list[str] = Field(default_factory=list)
    family: str | None = None
    max_output_tokens: int = Field(default=8192, ge=256)


class AssignmentConfig(BaseModel):
    roles: dict[str, str] = Field(default_factory=dict)
    domains: dict[str, str] = Field(default_factory=dict)
    task_types: dict[str, str] = Field(default_factory=dict)
    instances: dict[str, str] = Field(default_factory=dict)
    project_default: str = "@coder"
    global_default: str = "@coder"


class GatewayPolicyConfig(BaseModel):
    require_verifier_family_diversity: Literal["true", "warn", "false"] | bool = True
    upgrade_on_attempt: int = Field(default=2, ge=1)
    benchmark_pin_models: bool = False


class ModelsConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    profiles: dict[str, ModelProfile]
    assignments: AssignmentConfig
    policies: GatewayPolicyConfig = Field(default_factory=GatewayPolicyConfig)

    @model_validator(mode="after")
    def _validate_references(self) -> ModelsConfig:
        for profile_name, profile in self.profiles.items():
            if profile.provider not in self.providers:
                raise ValueError(
                    f"Profile '{profile_name}' references unknown provider '{profile.provider}'"
                )
            for fb in profile.fallbacks:
                if fb not in self.profiles:
                    raise ValueError(f"Profile '{profile_name}' references unknown fallback '{fb}'")
        for role, profile_name in self.assignments.roles.items():
            if profile_name not in self.profiles:
                raise ValueError(
                    f"Role '{role}' assignment references unknown profile '{profile_name}'"
                )
        return self


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=4200, ge=1024, le=65535)


class SandboxLimitsConfig(BaseModel):
    memory: str = "1536m"
    cpus: float = Field(default=1.5, gt=0.0)
    pids: int = Field(default=512, ge=32)
    node_max_old_space_mb: int = Field(default=768, ge=128)


class RuntimeConfig(BaseModel):
    max_heavy_exec_slots: int = Field(default=2, ge=1, le=8)
    max_concurrent_tasks: int = Field(default=4, ge=1, le=16)
    sandbox_limits: SandboxLimitsConfig = Field(default_factory=SandboxLimitsConfig)
    sandbox_network: Literal["internal_no_dns"] = "internal_no_dns"
    egress_proxy_allowlist: list[str] = Field(
        default_factory=lambda: [
            "pypi.org",
            "files.pythonhosted.org",
            "registry.npmjs.org",
        ]
    )


class EmbeddingsConfig(BaseModel):
    provider: Literal["fastembed_onnx"] = "fastembed_onnx"
    model: str = "BAAI/bge-small-en-v1.5"


class ObservabilityConfig(BaseModel):
    local_tracing: bool = True
    phoenix: bool = False
    langsmith_export: bool = False


def _default_backend_order() -> list[Literal["keyring", "encrypted_vault"]]:
    return ["keyring", "encrypted_vault"]


class SecretsConfig(BaseModel):
    backend_order: list[Literal["keyring", "encrypted_vault"]] = Field(
        default_factory=_default_backend_order
    )
    vault_path: str = "~/foundry/vault/credentials.enc"


class GlobalBudgetConfig(BaseModel):
    monthly_usd: float = Field(default=25.0, ge=0.0)


class FoundryGlobalConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
    budgets: GlobalBudgetConfig = Field(default_factory=GlobalBudgetConfig)


class DataPolicyConfig(BaseModel):
    allow_free_tier: bool = False
    allow_trace_export: bool = False


class AutonomyOverrides(BaseModel):
    strategic: DecisionMode | None = None
    tactical: DecisionMode | None = None
    operational: DecisionMode | None = None
    release_infra: DecisionMode | None = None


class AutonomyConfig(BaseModel):
    preset: AutonomyPreset = AutonomyPreset.BALANCED
    overrides: AutonomyOverrides = Field(default_factory=AutonomyOverrides)
    confidence_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    decision_timeout_minutes: int | None = Field(default=None, ge=1)


class TaskBudgetConfig(BaseModel):
    implement_usd: float = Field(default=0.60, ge=0.0)
    review_usd: float = Field(default=0.15, ge=0.0)


class FeatureBudgetConfig(BaseModel):
    blackbox_tests_usd: float = Field(default=0.30, ge=0.0)


class BudgetConfig(BaseModel):
    billing_enforcement: BillingEnforcement = BillingEnforcement.ACTUAL_USD
    per_run_usd: float = Field(default=5.00, ge=0.0)
    per_run_wall_clock_minutes: int = Field(default=90, ge=1)
    per_run_max_llm_calls: int = Field(default=600, ge=1)
    per_task: TaskBudgetConfig = Field(default_factory=TaskBudgetConfig)
    per_feature: FeatureBudgetConfig = Field(default_factory=FeatureBudgetConfig)
    per_domain_lead_usd: float = Field(default=0.60, ge=0.0)
    agent_step_limit: int = Field(default=25, ge=1)
    max_attempts_per_task: int = Field(default=3, ge=1)
    warn_at: float = Field(default=0.80, gt=0.0, le=1.0)


class DeployConfig(BaseModel):
    target: Literal["local_compose", "vercel_render_neon", "aws", "azure"] = "local_compose"


class GitConfig(BaseModel):
    remote: str = "github"
    repo: str = "<user>/app"


class ProjectConfig(BaseModel):
    name: str
    stack_pack: str = "fastapi-react-postgres"
    data_policy: DataPolicyConfig = Field(default_factory=DataPolicyConfig)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    deploy: DeployConfig = Field(default_factory=DeployConfig)
    git: GitConfig = Field(default_factory=GitConfig)
