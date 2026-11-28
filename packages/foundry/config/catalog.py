"""Canonical provider catalog, model compatibility validation, and official model discovery."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from foundry.config.schemas import BillingMode, DataTier, ProviderConfig, TransportKind


class ProviderCatalogEntry(BaseModel):
    """Unambiguous provider metadata and curated available models."""

    id: str
    label: str
    default_provider_id: str
    transport: TransportKind
    billing_mode: BillingMode
    family: str
    default_base_url: str | None = None
    supports_tool_calling: bool = True
    requires_api_key: bool = True
    allow_custom_model: bool = False
    models: list[str] = Field(default_factory=list)


PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = (
    ProviderCatalogEntry(
        id="gemini",
        label="Google Gemini",
        default_provider_id="gemini_paid",
        transport=TransportKind.GOOGLE_GENAI,
        billing_mode=BillingMode.PAID_API,
        family="gemini",
        supports_tool_calling=True,
        requires_api_key=True,
        allow_custom_model=False,
        models=[
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
        ],
    ),
    ProviderCatalogEntry(
        id="openai",
        label="OpenAI",
        default_provider_id="openai_paid",
        transport=TransportKind.OPENAI_COMPAT,
        billing_mode=BillingMode.PAID_API,
        family="openai",
        default_base_url="https://api.openai.com/v1",
        supports_tool_calling=True,
        requires_api_key=True,
        allow_custom_model=False,
        models=[
            "gpt-5",
            "gpt-5-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "o4-mini",
            "o3-mini",
        ],
    ),
    ProviderCatalogEntry(
        id="openai_codex_cli",
        label="OpenAI Subscription (Codex CLI)",
        default_provider_id="openai_subscription_cli",
        transport=TransportKind.OPENAI_CODEX_CLI,
        billing_mode=BillingMode.SUBSCRIPTION_CLI,
        family="openai",
        supports_tool_calling=False,
        requires_api_key=False,
        allow_custom_model=False,
        models=[
            "gpt-5",
            "gpt-5-codex",
            "o4-mini",
            "gpt-4.1",
        ],
    ),
    ProviderCatalogEntry(
        id="anthropic",
        label="Anthropic Claude",
        default_provider_id="anthropic_paid",
        transport=TransportKind.ANTHROPIC,
        billing_mode=BillingMode.PAID_API,
        family="anthropic",
        supports_tool_calling=True,
        requires_api_key=True,
        allow_custom_model=False,
        models=[
            "claude-sonnet-4-5",
            "claude-3-7-sonnet-latest",
            "claude-3-5-haiku-latest",
            "claude-opus-4-1",
        ],
    ),
    ProviderCatalogEntry(
        id="xai",
        label="xAI Grok",
        default_provider_id="xai_paid",
        transport=TransportKind.OPENAI_COMPAT,
        billing_mode=BillingMode.PAID_API,
        family="xai",
        default_base_url="https://api.x.ai/v1",
        supports_tool_calling=True,
        requires_api_key=True,
        allow_custom_model=False,
        models=[
            "grok-3",
            "grok-3-mini",
            "grok-2-latest",
        ],
    ),
    ProviderCatalogEntry(
        id="deepseek",
        label="DeepSeek",
        default_provider_id="deepseek_paid",
        transport=TransportKind.OPENAI_COMPAT,
        billing_mode=BillingMode.PAID_API,
        family="deepseek",
        default_base_url="https://api.deepseek.com/v1",
        supports_tool_calling=True,
        requires_api_key=True,
        allow_custom_model=False,
        models=[
            "deepseek-chat",
            "deepseek-reasoner",
        ],
    ),
    ProviderCatalogEntry(
        id="groq",
        label="Groq",
        default_provider_id="groq_paid",
        transport=TransportKind.OPENAI_COMPAT,
        billing_mode=BillingMode.PAID_API,
        family="groq",
        default_base_url="https://api.groq.com/openai/v1",
        supports_tool_calling=True,
        requires_api_key=True,
        allow_custom_model=True,
        models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "qwen-qwq-32b",
        ],
    ),
    ProviderCatalogEntry(
        id="openrouter",
        label="OpenRouter",
        default_provider_id="openrouter_optional",
        transport=TransportKind.OPENAI_COMPAT,
        billing_mode=BillingMode.PAID_API,
        family="openrouter",
        default_base_url="https://openrouter.ai/api/v1",
        supports_tool_calling=True,
        requires_api_key=True,
        allow_custom_model=True,
        models=[
            "openai/gpt-5",
            "anthropic/claude-sonnet-4.5",
            "google/gemini-2.5-pro",
            "deepseek/deepseek-chat-v3-0324",
        ],
    ),
    ProviderCatalogEntry(
        id="ollama",
        label="Ollama (Local)",
        default_provider_id="ollama_local",
        transport=TransportKind.OPENAI_COMPAT,
        billing_mode=BillingMode.LOCAL,
        family="local",
        default_base_url="http://localhost:11434/v1",
        supports_tool_calling=True,
        requires_api_key=False,
        allow_custom_model=True,
        models=[
            "qwen2.5-coder:14b",
            "llama3.2:latest",
            "deepseek-r1:14b",
        ],
    ),
    ProviderCatalogEntry(
        id="custom",
        label="Custom OpenAI-Compatible",
        default_provider_id="custom_compat",
        transport=TransportKind.OPENAI_COMPAT,
        billing_mode=BillingMode.PAID_API,
        family="custom",
        default_base_url="https://api.openai.com/v1",
        supports_tool_calling=True,
        requires_api_key=True,
        allow_custom_model=True,
        models=[],
    ),
)

_PROVIDER_BY_ID: dict[str, ProviderCatalogEntry] = {entry.id: entry for entry in PROVIDER_CATALOG}
_PROVIDER_BY_DEFAULT_ID: dict[str, ProviderCatalogEntry] = {
    entry.default_provider_id: entry for entry in PROVIDER_CATALOG
}
_PROVIDER_ALIASES: dict[str, str] = {
    "google": "gemini",
    "google_gemini": "gemini",
    "gemini_free": "gemini",
    "codex": "openai_codex_cli",
    "openai_subscription": "openai_codex_cli",
    "openai_subscription_cli": "openai_codex_cli",
    "claude": "anthropic",
    "anthropic_claude": "anthropic",
    "grok": "xai",
    "local": "ollama",
    "ollama_local": "ollama",
    "openrouter_optional": "openrouter",
}

_PROVIDER_PREFIX_RULES: dict[str, tuple[str, ...]] = {
    "gemini": ("gemini-",),
    "openai": ("gpt-", "o1", "o3", "o4", "codex-", "chatgpt-"),
    "openai_codex_cli": ("gpt-", "o1", "o3", "o4", "codex-"),
    "anthropic": ("claude-",),
    "xai": ("grok-",),
    "deepseek": ("deepseek-",),
}


def list_provider_catalog() -> list[ProviderCatalogEntry]:
    """Return the canonical catalog of supported official providers."""
    return list(PROVIDER_CATALOG)


def get_provider_catalog_entry(provider_id: str) -> ProviderCatalogEntry | None:
    """Resolve a canonical provider ID, alias, or `models.yaml` provider key to a catalog entry."""
    norm = provider_id.strip().lower()
    if norm in _PROVIDER_BY_ID:
        return _PROVIDER_BY_ID[norm]
    if norm in _PROVIDER_BY_DEFAULT_ID:
        return _PROVIDER_BY_DEFAULT_ID[norm]
    aliased = _PROVIDER_ALIASES.get(norm)
    if aliased:
        return _PROVIDER_BY_ID.get(aliased)
    return None


def provider_config_from_catalog(
    entry: ProviderCatalogEntry,
    *,
    key_ref: str | None = None,
    base_url: str | None = None,
) -> ProviderConfig:
    """Construct a valid `ProviderConfig` matching a catalog entry."""
    data_tier = DataTier.LOCAL if entry.billing_mode == BillingMode.LOCAL else DataTier.PAID
    return ProviderConfig(
        transport=entry.transport,
        billing_mode=entry.billing_mode,
        base_url=base_url if base_url is not None else entry.default_base_url,
        key_ref=key_ref,
        family=entry.family,
        data_tier=data_tier,
        supports_tool_calling=entry.supports_tool_calling,
    )


def is_model_compatible_with_provider(
    provider_id: str,
    model_name: str,
    extra_allowed: Iterable[str] = (),
) -> bool:
    """Return True if `model_name` is compatible with `provider_id`."""
    cleaned = model_name.strip()
    if not cleaned:
        return False
    entry = get_provider_catalog_entry(provider_id)
    if entry is None:
        return True
    if entry.allow_custom_model:
        return True
    allowed_exact = {m.lower() for m in entry.models} | {m.strip().lower() for m in extra_allowed}
    lowered = cleaned.lower()
    if lowered in allowed_exact:
        return True
    prefixes = _PROVIDER_PREFIX_RULES.get(entry.id)
    if prefixes is None:
        return True
    return any(lowered.startswith(prefix) for prefix in prefixes)


async def discover_provider_models(
    provider: str,
    secret: str | None,
    *,
    base_url: str | None = None,
    timeout_s: float = 8.0,
) -> tuple[list[str], Literal["live_api", "catalog_fallback"], str | None]:
    """Query official provider `/models` endpoints with graceful curated fallback offline."""
    entry = get_provider_catalog_entry(provider)
    fallback_models = list(entry.models) if entry is not None else []
    if entry is None or (entry.requires_api_key and not secret):
        detail = (
            None if entry is None else "Vault locked or API key secret unavailable; using catalog."
        )
        return fallback_models, "catalog_fallback", detail

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            if entry.id == "gemini":
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": secret or ""},
                )
                resp.raise_for_status()
                raw_items = resp.json().get("models", [])
                discovered: list[str] = []
                for item in raw_items:
                    methods = item.get("supportedGenerationMethods") or []
                    if methods and "generateContent" not in methods:
                        continue
                    raw_name = str(item.get("name", ""))
                    short_name = raw_name.removeprefix("models/").strip()
                    if short_name.startswith("gemini-") and short_name not in discovered:
                        discovered.append(short_name)
                if discovered:
                    return discovered, "live_api", None

            elif entry.id == "anthropic":
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": secret or "",
                        "anthropic-version": "2023-06-01",
                    },
                )
                resp.raise_for_status()
                raw_items = resp.json().get("data", [])
                discovered = [
                    str(item.get("id", "")).strip()
                    for item in raw_items
                    if str(item.get("id", "")).strip()
                ]
                if discovered:
                    return discovered, "live_api", None

            elif entry.transport == TransportKind.OPENAI_COMPAT:
                target_base = (base_url or entry.default_base_url or "").rstrip("/")
                if target_base:
                    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
                    resp = await client.get(f"{target_base}/models", headers=headers)
                    resp.raise_for_status()
                    raw_items = resp.json().get("data", [])
                    discovered = []
                    for item in raw_items:
                        mid = str(item.get("id", "")).strip()
                        if not mid:
                            continue
                        if entry.id == "openai" and not is_model_compatible_with_provider(
                            "openai", mid
                        ):
                            continue
                        if entry.id == "xai" and not is_model_compatible_with_provider("xai", mid):
                            continue
                        if mid not in discovered:
                            discovered.append(mid)
                    if discovered:
                        return sorted(discovered), "live_api", None
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return fallback_models, "catalog_fallback", str(exc)

    return fallback_models, "catalog_fallback", None
