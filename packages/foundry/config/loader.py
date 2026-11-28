"""Default builders and YAML file loaders for Foundry configuration."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from foundry.config.schemas import (
    AgentRosterConfig,
    AgentSlotConfig,
    ApiKeysRegistryConfig,
    AssignmentConfig,
    BillingMode,
    DataTier,
    FoundryGlobalConfig,
    GatewayPolicyConfig,
    ModelProfile,
    ModelsConfig,
    ProjectConfig,
    ProviderConfig,
    TokenPrice,
    TransportKind,
)

CONFIG_DIR_DEFAULT = Path.home() / "foundry" / "config"


class ConfigError(Exception):
    """Raised when configuration files cannot be read or fail validation."""


def default_foundry_config() -> FoundryGlobalConfig:
    return FoundryGlobalConfig()


def default_models_config() -> ModelsConfig:
    """Return the default v1.4 first-party SDK models configuration (zero LiteLLM)."""
    providers = {
        "gemini_paid": ProviderConfig(
            transport=TransportKind.GOOGLE_GENAI,
            billing_mode=BillingMode.PAID_API,
            key_ref="vault:gemini",
            family="gemini",
            data_tier=DataTier.PAID,
        ),
        "gemini_free": ProviderConfig(
            transport=TransportKind.GOOGLE_GENAI,
            billing_mode=BillingMode.FREE_TIER,
            key_ref="vault:gemini_free",
            family="gemini",
            data_tier=DataTier.FREE_TRAINING_ALLOWED,
        ),
        "openai_paid": ProviderConfig(
            transport=TransportKind.OPENAI_COMPAT,
            base_url="https://api.openai.com/v1",
            billing_mode=BillingMode.PAID_API,
            key_ref="vault:openai",
            family="openai",
            data_tier=DataTier.PAID,
        ),
        "xai_paid": ProviderConfig(
            transport=TransportKind.OPENAI_COMPAT,
            base_url="https://api.x.ai/v1",
            billing_mode=BillingMode.PAID_API,
            key_ref="vault:xai",
            family="xai",
            data_tier=DataTier.PAID,
        ),
        "anthropic_paid": ProviderConfig(
            transport=TransportKind.ANTHROPIC,
            billing_mode=BillingMode.PAID_API,
            key_ref="vault:anthropic",
            family="anthropic",
            data_tier=DataTier.PAID,
        ),
        "deepseek_paid": ProviderConfig(
            transport=TransportKind.OPENAI_COMPAT,
            base_url="https://api.deepseek.com/v1",
            billing_mode=BillingMode.PAID_API,
            key_ref="vault:deepseek",
            family="deepseek",
            data_tier=DataTier.PAID,
        ),
        "groq_paid": ProviderConfig(
            transport=TransportKind.OPENAI_COMPAT,
            base_url="https://api.groq.com/openai/v1",
            billing_mode=BillingMode.PAID_API,
            key_ref="vault:groq",
            family="groq",
            data_tier=DataTier.PAID,
        ),
        "openrouter_optional": ProviderConfig(
            transport=TransportKind.OPENAI_COMPAT,
            base_url="https://openrouter.ai/api/v1",
            billing_mode=BillingMode.PAID_API,
            key_ref="vault:openrouter",
            family="openrouter",
            data_tier=DataTier.PAID,
        ),
        "ollama_local": ProviderConfig(
            transport=TransportKind.OPENAI_COMPAT,
            base_url="http://localhost:11434/v1",
            billing_mode=BillingMode.LOCAL,
            family="local",
            data_tier=DataTier.LOCAL,
        ),
        "openai_subscription_cli": ProviderConfig(
            transport=TransportKind.OPENAI_CODEX_CLI,
            billing_mode=BillingMode.SUBSCRIPTION_CLI,
            family="openai",
            data_tier=DataTier.PAID,
            supports_tool_calling=False,
        ),
    }

    profiles = {
        "@planner": ModelProfile(
            provider="gemini_paid",
            model="gemini-2.5-pro",
            family="gemini",
            price=TokenPrice(**{"in": 2.00, "cached_in": 0.20, "out": 12.00}),
            fallbacks=["@planner_openai"],
        ),
        "@planner_openai": ModelProfile(
            provider="openai_paid",
            model="gpt-5",
            family="openai",
            price=TokenPrice(**{"in": 2.00, "cached_in": 0.50, "out": 8.00}),
        ),
        "@openai_subscription": ModelProfile(
            provider="openai_subscription_cli",
            model="gpt-5",
            family="openai",
            price=TokenPrice(**{"in": 0.0, "cached_in": 0.0, "out": 0.0}),
        ),
        "@lead": ModelProfile(
            provider="gemini_paid",
            model="gemini-2.5-flash",
            family="gemini",
            price=TokenPrice(**{"in": 1.50, "cached_in": 0.15, "out": 7.50}),
            fallbacks=["@lead_openai"],
        ),
        "@lead_openai": ModelProfile(
            provider="openai_paid",
            model="gpt-4.1-mini",
            family="openai",
            price=TokenPrice(**{"in": 1.50, "cached_in": 0.30, "out": 6.00}),
        ),
        "@coder_plus": ModelProfile(
            provider="gemini_paid",
            model="gemini-2.5-flash",
            family="gemini",
            price=TokenPrice(**{"in": 1.50, "cached_in": 0.15, "out": 7.50}),
            fallbacks=["@lead_openai"],
        ),
        "@coder": ModelProfile(
            provider="gemini_paid",
            model="gemini-2.5-flash-lite",
            family="gemini",
            price=TokenPrice(**{"in": 0.25, "cached_in": 0.025, "out": 1.50}),
            fallbacks=["@coder_deepseek"],
        ),
        "@coder_deepseek": ModelProfile(
            provider="deepseek_paid",
            model="deepseek-chat",
            family="deepseek",
            price=TokenPrice(**{"in": 0.27, "cached_in": 0.07, "out": 1.10}),
        ),
        "@verifier": ModelProfile(
            provider="xai_paid",
            model="grok-3-mini",
            family="xai",
            price=TokenPrice(**{"in": 1.25, "cached_in": 0.20, "out": 2.50}),
            fallbacks=["@verifier_openai"],
        ),
        "@verifier_openai": ModelProfile(
            provider="openai_paid",
            model="gpt-4.1-mini",
            family="openai",
            price=TokenPrice(**{"in": 1.25, "cached_in": 0.20, "out": 2.50}),
        ),
        "@fast": ModelProfile(
            provider="gemini_paid",
            model="gemini-2.5-flash-lite",
            family="gemini",
            price=TokenPrice(**{"in": 0.25, "cached_in": 0.025, "out": 1.50}),
        ),
    }

    assignments = AssignmentConfig(
        roles={
            "orchestrator": "@planner",
            "lead": "@lead",
            "implementer": "@coder",
            "test_writer": "@verifier",
            "reviewer": "@verifier",
            "integration": "@coder_plus",
            "release": "@fast",
        }
    )

    return ModelsConfig(
        providers=providers,
        profiles=profiles,
        assignments=assignments,
        policies=GatewayPolicyConfig(),
    )


def default_project_config(name: str = "app") -> ProjectConfig:
    return ProjectConfig(name=name)


def default_api_keys_registry() -> ApiKeysRegistryConfig:
    return ApiKeysRegistryConfig(keys=[])


def default_agent_roster_config() -> AgentRosterConfig:
    """Return Foundry's default 3-layer agent hierarchy (Orchestrator -> Leads -> Workers)."""
    return AgentRosterConfig(
        agents=[
            AgentSlotConfig(
                id="orch_primary",
                name="Project Orchestrator",
                layer=1,
                role="orchestrator",
                domain=None,
                parent_id=None,
                model_profile="@planner",
            ),
            AgentSlotConfig(
                id="lead_backend",
                name="Backend Lead",
                layer=2,
                role="lead",
                domain="backend",
                parent_id="orch_primary",
                model_profile="@lead",
            ),
            AgentSlotConfig(
                id="lead_frontend",
                name="Frontend Lead",
                layer=2,
                role="lead",
                domain="frontend",
                parent_id="orch_primary",
                model_profile="@lead",
            ),
            AgentSlotConfig(
                id="impl_backend",
                name="Backend Implementer",
                layer=3,
                role="implementer",
                domain="backend",
                parent_id="lead_backend",
                model_profile="@coder",
            ),
            AgentSlotConfig(
                id="impl_frontend",
                name="Frontend Implementer",
                layer=3,
                role="implementer",
                domain="frontend",
                parent_id="lead_frontend",
                model_profile="@coder",
            ),
            AgentSlotConfig(
                id="g3_verifier",
                name="G3 Black-Box Test Writer",
                layer=3,
                role="test_writer",
                domain="backend",
                parent_id="lead_backend",
                model_profile="@verifier",
            ),
            AgentSlotConfig(
                id="g4_reviewer",
                name="G4 Cross-Family Reviewer",
                layer=3,
                role="reviewer",
                domain="frontend",
                parent_id="lead_frontend",
                model_profile="@verifier",
            ),
        ]
    )


def _load_yaml_model[TModel: BaseModel](path: Path, model_cls: type[TModel]) -> TModel:
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Failed to read YAML from {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Expected mapping in {path}, got {type(raw).__name__}")
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {path}:\n{exc}") from exc


def _save_yaml_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def load_foundry_config(path: Path | None = None) -> FoundryGlobalConfig:
    target = path or (CONFIG_DIR_DEFAULT / "foundry.yaml")
    return _load_yaml_model(target, FoundryGlobalConfig)


def save_foundry_config(config: FoundryGlobalConfig, path: Path | None = None) -> Path:
    target = path or (CONFIG_DIR_DEFAULT / "foundry.yaml")
    _save_yaml_model(target, config)
    return target


def load_models_config(path: Path | None = None) -> ModelsConfig:
    target = path or (CONFIG_DIR_DEFAULT / "models.yaml")
    return _load_yaml_model(target, ModelsConfig)


def ensure_subscription_provider(cfg: ModelsConfig) -> ModelsConfig:
    """Ensure `openai_subscription_cli` provider exists on older `models.yaml` files."""
    if "openai_subscription_cli" in cfg.providers:
        return cfg
    updated_providers = dict(cfg.providers)
    updated_providers["openai_subscription_cli"] = ProviderConfig(
        transport=TransportKind.OPENAI_CODEX_CLI,
        billing_mode=BillingMode.SUBSCRIPTION_CLI,
        family="openai",
        data_tier=DataTier.PAID,
        supports_tool_calling=False,
    )
    updated_profiles = dict(cfg.profiles)
    if "@openai_subscription" not in updated_profiles:
        updated_profiles["@openai_subscription"] = ModelProfile(
            provider="openai_subscription_cli",
            model="gpt-5",
            family="openai",
            price=TokenPrice(**{"in": 0.0, "cached_in": 0.0, "out": 0.0}),
        )
    return ModelsConfig(
        providers=updated_providers,
        profiles=updated_profiles,
        assignments=cfg.assignments,
        policies=cfg.policies,
    )


def save_models_config(config: ModelsConfig, path: Path | None = None) -> Path:
    target = path or (CONFIG_DIR_DEFAULT / "models.yaml")
    _save_yaml_model(target, config)
    return target


def load_project_config(path: Path | None = None) -> ProjectConfig:
    target = path or (Path.cwd() / ".foundry" / "project.yaml")
    return _load_yaml_model(target, ProjectConfig)


def save_project_config(config: ProjectConfig, path: Path | None = None) -> Path:
    target = path or (Path.cwd() / ".foundry" / "project.yaml")
    _save_yaml_model(target, config)
    return target


def load_api_keys_registry(path: Path | None = None) -> ApiKeysRegistryConfig:
    target = path or (CONFIG_DIR_DEFAULT / "api_keys.yaml")
    return _load_yaml_model(target, ApiKeysRegistryConfig)


def save_api_keys_registry(config: ApiKeysRegistryConfig, path: Path | None = None) -> Path:
    target = path or (CONFIG_DIR_DEFAULT / "api_keys.yaml")
    _save_yaml_model(target, config)
    return target


def load_agent_roster_config(path: Path | None = None) -> AgentRosterConfig:
    target = path or (CONFIG_DIR_DEFAULT / "agents.yaml")
    return _load_yaml_model(target, AgentRosterConfig)


def save_agent_roster_config(config: AgentRosterConfig, path: Path | None = None) -> Path:
    target = path or (CONFIG_DIR_DEFAULT / "agents.yaml")
    _save_yaml_model(target, config)
    return target
