"""First-party official Anthropic SDK transport with cache_control breakpoints."""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import Callable
from typing import Any

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic
from anthropic import RateLimitError as AnthropicRateLimitError

from foundry.config.schemas import ProviderConfig, TransportKind
from foundry.gateway.protocol import (
    GatewayRequest,
    GatewayResponse,
    RateLimitError,
    ToolCallSpec,
    TransportError,
    TransportHealth,
)
from foundry.security.vault import CredentialVault


class AnthropicTransport:
    """Thin adapter around anthropic.AsyncAnthropic with ephemeral cache_control."""

    name = TransportKind.ANTHROPIC.value

    def __init__(
        self,
        vault: CredentialVault,
        client_factory: Callable[..., AsyncAnthropic] | None = None,
    ) -> None:
        self._vault = vault
        self._client_factory = client_factory or AsyncAnthropic
        self._clients: dict[tuple[str, str], AsyncAnthropic] = {}

    def _build_client(self, provider_id: str, cfg: ProviderConfig) -> AsyncAnthropic:
        key_name = cfg.key_ref or provider_id
        api_key = self._vault.get_secret(key_name)
        if not api_key:
            raise TransportError(f"Missing API key for provider '{provider_id}' ({key_name})")
        cache_key = (provider_id, api_key)
        if cache_key not in self._clients:
            self._clients[cache_key] = self._client_factory(api_key=api_key)
        return self._clients[cache_key]

    async def aclose(self) -> None:
        for client in self._clients.values():
            close_fn = getattr(client, "close", None)
            if callable(close_fn):
                res = close_fn()
                if inspect.isawaitable(res):
                    await res
        self._clients.clear()

    @staticmethod
    def _parse_tool_call(tc: dict[str, Any], index: int) -> tuple[str, str, dict[str, Any]]:
        tc_id = str(tc.get("id") or f"call_{index}")
        fn = tc.get("function")
        if isinstance(fn, dict):
            name = str(fn.get("name", ""))
            raw_args = fn.get("arguments", {})
        else:
            name = str(tc.get("name", ""))
            raw_args = tc.get("args", {})
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                parsed = {}
            args = dict(parsed) if isinstance(parsed, dict) else {}
        elif isinstance(raw_args, dict):
            args = dict(raw_args)
        else:
            args = {}
        return tc_id, name, args

    @classmethod
    def _convert_messages(
        cls,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        system_blocks: list[dict[str, Any]] = []
        convo: list[dict[str, Any]] = []

        def _append_turn(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            if convo and convo[-1]["role"] == role:
                convo[-1]["content"].extend(blocks)
            else:
                convo.append({"role": role, "content": list(blocks)})

        for msg in messages:
            role = msg.get("role", "user")
            raw_content = msg.get("content")
            content = str(raw_content) if raw_content is not None else ""

            if role == "system":
                if content:
                    system_blocks.append(
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    )
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for idx, tc in enumerate(msg.get("tool_calls") or []):
                    if isinstance(tc, dict):
                        tc_id, tc_name, tc_args = cls._parse_tool_call(tc, idx)
                        if tc_name:
                            blocks.append(
                                {
                                    "type": "tool_use",
                                    "id": tc_id,
                                    "name": tc_name,
                                    "input": tc_args,
                                }
                            )
                _append_turn("assistant", blocks)
            elif role == "tool":
                tool_use_id = str(msg.get("tool_call_id") or msg.get("id") or "call_0")
                _append_turn(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": content,
                        }
                    ],
                )
            else:
                _append_turn("user", [{"type": "text", "text": content}])

        return system_blocks, convo

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            fn = tool.get("function", tool)
            converted.append(
                {
                    "name": str(fn["name"]),
                    "description": str(fn.get("description", "")),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return converted

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        client = self._build_client(provider_id, provider_cfg)
        start = time.perf_counter()
        system_blocks, convo = self._convert_messages(request.messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": convo,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if system_blocks:
            kwargs["system"] = system_blocks
        if request.tools:
            kwargs["tools"] = self._convert_tools(request.tools)

        try:
            raw = await client.messages.create(**kwargs)
        except AnthropicRateLimitError as exc:
            raise RateLimitError(str(exc)) from exc
        except (APIConnectionError, APIStatusError) as exc:
            raise TransportError(f"Anthropic request failed: {exc}") from exc

        latency_ms = int((time.perf_counter() - start) * 1000)
        text_parts: list[str] = []
        tool_calls: list[ToolCallSpec] = []
        for block in getattr(raw, "content", None) or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(str(getattr(block, "text", "")))
            elif block_type == "tool_use":
                raw_input = getattr(block, "input", {}) or {}
                tool_calls.append(
                    ToolCallSpec(
                        id=str(getattr(block, "id", f"call_{len(tool_calls)}")),
                        name=str(getattr(block, "name", "")),
                        args=dict(raw_input) if isinstance(raw_input, dict) else {},
                    )
                )

        usage = getattr(raw, "usage", None)
        uncached_in = int(getattr(usage, "input_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0)

        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=TransportKind.ANTHROPIC,
            billing_mode=provider_cfg.billing_mode,
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            tokens_in=uncached_in + cache_read + cache_write,
            tokens_cached=cache_read,
            tokens_cache_write=cache_write,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            family=provider_cfg.family or "anthropic",
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
                transport=TransportKind.ANTHROPIC,
                healthy=False,
                detail=str(exc),
            )
        return TransportHealth(
            provider_id=provider_id,
            transport=TransportKind.ANTHROPIC,
            healthy=True,
            detail="configured (anthropic SDK)",
        )
