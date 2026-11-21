"""First-party official OpenAI SDK transport (OpenAI, xAI, Groq, DeepSeek, Ollama, etc.)."""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import Callable
from typing import Any

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from openai import RateLimitError as OpenAIRateLimitError

from foundry.config.schemas import BillingMode, ProviderConfig, TransportKind
from foundry.gateway.protocol import (
    GatewayRequest,
    GatewayResponse,
    RateLimitError,
    ToolCallSpec,
    TransportError,
    TransportHealth,
)
from foundry.security.vault import CredentialVault


class OpenAICompatibleTransport:
    """Thin adapter around openai.AsyncOpenAI with configurable base_url."""

    name = TransportKind.OPENAI_COMPAT.value

    def __init__(
        self,
        vault: CredentialVault,
        client_factory: Callable[..., AsyncOpenAI] | None = None,
    ) -> None:
        self._vault = vault
        self._client_factory = client_factory or AsyncOpenAI
        self._clients: dict[tuple[str, str, str], AsyncOpenAI] = {}

    def _build_client(self, provider_id: str, cfg: ProviderConfig) -> AsyncOpenAI:
        api_key = "ollama"
        if cfg.billing_mode != BillingMode.LOCAL:
            key_name = cfg.key_ref or provider_id
            resolved = self._vault.get_secret(key_name)
            if not resolved:
                raise TransportError(f"Missing API key for provider '{provider_id}' ({key_name})")
            api_key = resolved
        cache_key = (provider_id, cfg.base_url or "", api_key)
        if cache_key not in self._clients:
            kwargs: dict[str, Any] = {"api_key": api_key}
            if cfg.base_url:
                kwargs["base_url"] = cfg.base_url
            self._clients[cache_key] = self._client_factory(**kwargs)
        return self._clients[cache_key]

    async def aclose(self) -> None:
        for client in self._clients.values():
            close_fn = getattr(client, "close", None)
            if callable(close_fn):
                res = close_fn()
                if inspect.isawaitable(res):
                    await res
        self._clients.clear()

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        client = self._build_client(provider_id, provider_cfg)
        start = time.perf_counter()
        use_max_completion_tokens = provider_cfg.family == "openai" and model.startswith(
            ("o1", "o3", "o4", "gpt-5")
        )
        token_param = "max_completion_tokens" if use_max_completion_tokens else "max_tokens"
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": request.messages,
            "temperature": request.temperature,
            token_param: request.max_tokens,
        }
        if request.tools:
            kwargs["tools"] = request.tools

        try:
            raw = await client.chat.completions.create(**kwargs)
        except OpenAIRateLimitError as exc:
            raise RateLimitError(str(exc)) from exc
        except (APIConnectionError, APIStatusError) as exc:
            raise TransportError(f"OpenAI-compatible request failed: {exc}") from exc

        latency_ms = int((time.perf_counter() - start) * 1000)
        choice = raw.choices[0]
        message = choice.message
        tool_calls: list[ToolCallSpec] = []
        for idx, tc in enumerate(getattr(message, "tool_calls", None) or []):
            fn = tc.function
            raw_args = getattr(fn, "arguments", None)
            if isinstance(raw_args, str):
                if not raw_args.strip():
                    parsed_args: Any = {}
                else:
                    try:
                        parsed_args = json.loads(raw_args)
                    except json.JSONDecodeError as exc:
                        raise TransportError(
                            f"Malformed JSON tool arguments from {provider_id}/{model} "
                            f"for '{fn.name}': {exc}"
                        ) from exc
            elif isinstance(raw_args, dict):
                parsed_args = raw_args
            else:
                parsed_args = {}
            tool_calls.append(
                ToolCallSpec(
                    id=getattr(tc, "id", None) or f"call_{idx}",
                    name=fn.name,
                    args=parsed_args if isinstance(parsed_args, dict) else {},
                )
            )

        usage = getattr(raw, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
        details = getattr(usage, "prompt_tokens_details", None)
        tokens_cached = int(getattr(details, "cached_tokens", 0) or 0)

        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=TransportKind.OPENAI_COMPAT,
            billing_mode=provider_cfg.billing_mode,
            content=message.content or "",
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_cached=tokens_cached,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            family=provider_cfg.family,
        )

    async def health_check(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
    ) -> TransportHealth:
        try:
            _ = self._build_client(provider_id, provider_cfg)
        except TransportError as exc:
            return TransportHealth(
                provider_id=provider_id,
                transport=TransportKind.OPENAI_COMPAT,
                healthy=False,
                detail=str(exc),
            )
        return TransportHealth(
            provider_id=provider_id,
            transport=TransportKind.OPENAI_COMPAT,
            healthy=True,
            detail=f"configured ({provider_cfg.base_url or 'https://api.openai.com/v1'})",
        )
