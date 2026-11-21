"""ModelTransport Protocol and gateway request/response contracts."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from foundry.config.schemas import BillingMode, ProviderConfig, TransportKind


class ToolCallSpec(BaseModel):
    """Normalized tool call emitted by any first-party SDK transport."""

    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class GatewayRequest(BaseModel):
    """Unified request sent through ModelGateway."""

    role: str
    domain: str | None = None
    feature: str | None = None
    task_id: str | None = None
    task_slug: str | None = None
    node: str | None = None
    step_index: int = Field(default=0, ge=0)
    attempt: int = Field(default=1, ge=1)
    agent_instance: str | None = None
    task_type: str | None = None
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)
    peer_implementer_family: str | None = None


class GatewayResponse(BaseModel):
    """Unified response returned by ModelGateway with exact provider token/cache telemetry."""

    provider_id: str
    model: str
    transport: TransportKind
    billing_mode: BillingMode
    content: str = ""
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_cached: int = 0
    tokens_cache_write: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    est_value_usd: float = 0.0
    prompt_hash: str = ""
    replayed: bool = False
    family: str | None = None
    ratelimit_backoff_ms: int = 0


class TransportHealth(BaseModel):
    provider_id: str
    transport: TransportKind
    healthy: bool
    detail: str = ""


class TransportError(Exception):
    """Raised when an official SDK call fails unexpectedly."""


class RateLimitError(TransportError):
    """Raised when a provider returns HTTP 429 or rate-limit exhaustion."""

    def __init__(self, message: str, *, retry_after_s: int | None = None) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(message)


@runtime_checkable
class ModelTransport(Protocol):
    """Pluggable first-party SDK transport interface."""

    name: str

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse: ...

    async def health_check(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
    ) -> TransportHealth: ...


def to_langchain_ai_message(response: GatewayResponse) -> AIMessage:
    """Convert a GatewayResponse into a standard LangGraph AIMessage."""
    lc_tool_calls = [
        {"id": tc.id, "name": tc.name, "args": tc.args, "type": "tool_call"}
        for tc in response.tool_calls
    ]
    return AIMessage(
        content=response.content,
        tool_calls=lc_tool_calls,
        response_metadata={
            "provider_id": response.provider_id,
            "model": response.model,
            "transport": str(response.transport),
            "tokens_in": response.tokens_in,
            "tokens_cached": response.tokens_cached,
            "tokens_cache_write": response.tokens_cache_write,
            "tokens_out": response.tokens_out,
            "cost_usd": response.cost_usd,
            "replayed": response.replayed,
        },
    )
