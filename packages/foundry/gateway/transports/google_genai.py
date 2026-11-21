"""First-party official Google GenAI SDK transport (`google-genai`)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

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


class GoogleGenAITransport:
    """Thin adapter around the official google-genai SDK."""

    name = TransportKind.GOOGLE_GENAI.value

    def __init__(
        self,
        vault: CredentialVault,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._vault = vault
        self._client_factory = client_factory or genai.Client
        self._clients: dict[tuple[str, str], Any] = {}

    def _build_client(self, provider_id: str, cfg: ProviderConfig) -> Any:
        key_name = cfg.key_ref or provider_id
        api_key = self._vault.get_secret(key_name)
        if not api_key:
            raise TransportError(f"Missing API key for provider '{provider_id}' ({key_name})")
        cache_key = (provider_id, api_key)
        if cache_key not in self._clients:
            self._clients[cache_key] = self._client_factory(api_key=api_key)
        return self._clients[cache_key]

    async def aclose(self) -> None:
        self._clients.clear()

    @staticmethod
    def _parse_tool_call(tc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
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
        return name, args

    @classmethod
    def _convert_messages(
        cls,
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        pending_tool_parts: list[dict[str, Any]] = []

        def _flush_tool_parts() -> None:
            if pending_tool_parts:
                contents.append({"role": "user", "parts": list(pending_tool_parts)})
                pending_tool_parts.clear()

        for msg in messages:
            role = msg.get("role", "user")
            raw_content = msg.get("content")
            content = str(raw_content) if raw_content is not None else ""

            if role == "system":
                _flush_tool_parts()
                if content:
                    system_parts.append(content)
            elif role == "tool":
                pending_tool_parts.append(
                    {
                        "function_response": {
                            "name": str(msg.get("name", "tool")),
                            "response": {"output": content},
                        }
                    }
                )
            elif role == "assistant":
                _flush_tool_parts()
                parts: list[dict[str, Any]] = []
                if content:
                    parts.append({"text": content})
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        tc_name, tc_args = cls._parse_tool_call(tc)
                        if tc_name:
                            parts.append({"function_call": {"name": tc_name, "args": tc_args}})
                if parts:
                    contents.append({"role": "model", "parts": parts})
            else:
                _flush_tool_parts()
                contents.append({"role": "user", "parts": [{"text": content}]})

        _flush_tool_parts()
        system_instruction = "\n\n".join(system_parts) if system_parts else None
        return system_instruction, contents

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[genai_types.Tool]:
        declarations: list[genai_types.FunctionDeclaration] = []
        for tool in tools:
            fn = tool.get("function", tool)
            declarations.append(
                genai_types.FunctionDeclaration(
                    name=str(fn["name"]),
                    description=str(fn.get("description", "")),
                    parameters=fn.get("parameters"),
                )
            )
        return [genai_types.Tool(function_declarations=declarations)] if declarations else []

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        client = self._build_client(provider_id, provider_cfg)
        start = time.perf_counter()
        system_instruction, contents = self._convert_messages(request.messages)
        config_kwargs: dict[str, Any] = {
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens,
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if request.tools:
            config_kwargs["tools"] = self._convert_tools(request.tools)

        try:
            raw = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
        except genai_errors.APIError as exc:
            if getattr(exc, "code", None) == 429:
                raise RateLimitError(str(exc)) from exc
            raise TransportError(f"Google GenAI request failed: {exc}") from exc

        latency_ms = int((time.perf_counter() - start) * 1000)
        text_parts: list[str] = []
        tool_calls: list[ToolCallSpec] = []
        candidates = getattr(raw, "candidates", None) or []
        if candidates and getattr(candidates[0], "content", None):
            parts = getattr(candidates[0].content, "parts", None) or []
            for idx, part in enumerate(parts):
                if getattr(part, "text", None):
                    text_parts.append(str(part.text))
                fn_call = getattr(part, "function_call", None)
                if fn_call is not None:
                    tool_calls.append(
                        ToolCallSpec(
                            id=f"call_{idx}",
                            name=str(fn_call.name),
                            args=dict(fn_call.args or {}),
                        )
                    )
        elif getattr(raw, "text", None):
            text_parts.append(str(raw.text))

        usage = getattr(raw, "usage_metadata", None)
        tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
        tokens_cached = int(getattr(usage, "cached_content_token_count", 0) or 0)
        tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0)

        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=TransportKind.GOOGLE_GENAI,
            billing_mode=provider_cfg.billing_mode,
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_cached=tokens_cached,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            family=provider_cfg.family or "gemini",
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
                transport=TransportKind.GOOGLE_GENAI,
                healthy=False,
                detail=str(exc),
            )
        return TransportHealth(
            provider_id=provider_id,
            transport=TransportKind.GOOGLE_GENAI,
            healthy=True,
            detail="configured (google-genai SDK)",
        )
