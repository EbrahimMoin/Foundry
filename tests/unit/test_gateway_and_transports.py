"""Unit tests for the 3 first-party SDK transports and ModelGateway routing/policies."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from foundry.config import (
    BudgetConfig,
    DataPolicyConfig,
    ModelsConfig,
    ProviderConfig,
    TokenPrice,
    TransportKind,
    default_models_config,
)
from foundry.gateway import (
    BudgetExceededError,
    CassetteStore,
    GatewayPolicyError,
    GatewayRequest,
    GatewayResponse,
    MissPolicy,
    ModelGateway,
    RateLimitError,
    RunBudgetState,
    TransportError,
    to_langchain_ai_message,
)
from foundry.gateway.transports import (
    AnthropicTransport,
    GoogleGenAITransport,
    OpenAICodexCliTransport,
    OpenAICompatibleTransport,
)
from foundry.security import CredentialVault, SecretLeakError


def _fake_vault() -> CredentialVault:
    vault = CredentialVault(backend_order=("encrypted_vault",))
    # Monkeypatch get_secret for deterministic offline tests
    vault.get_secret = lambda name, *, allow_env_fallback=True: "dummy-api-key"  # type: ignore[method-assign]
    return vault


@pytest.mark.asyncio
async def test_openai_compat_transport_extracts_tools_and_cached_tokens() -> None:
    async def _fake_create(**kwargs: Any) -> Any:
        assert kwargs["model"] == "gpt-4.1-mini"
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="Calling read_file",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_oai_1",
                                function=SimpleNamespace(
                                    name="read_file",
                                    arguments='{"path": "backend/app/main.py"}',
                                ),
                            )
                        ],
                    )
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=40,
                prompt_tokens_details=SimpleNamespace(cached_tokens=800),
            ),
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_fake_create))
    )
    transport = OpenAICompatibleTransport(
        _fake_vault(),
        client_factory=lambda **_: fake_client,  # type: ignore[arg-type,return-value]
    )
    cfg = default_models_config().providers["openai_paid"]
    resp = await transport.complete(
        "openai_paid",
        cfg,
        "gpt-4.1-mini",
        GatewayRequest(role="implementer", messages=[{"role": "user", "content": "hi"}]),
    )
    assert resp.tokens_in == 1000
    assert resp.tokens_cached == 800
    assert resp.tokens_out == 40
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].args == {"path": "backend/app/main.py"}

    ai_msg = to_langchain_ai_message(resp)
    assert ai_msg.tool_calls[0]["name"] == "read_file"
    assert ai_msg.tool_calls[0]["args"] == {"path": "backend/app/main.py"}


@pytest.mark.asyncio
async def test_google_genai_transport_extracts_cached_tokens_and_calls() -> None:
    async def _fake_gen(**kwargs: Any) -> Any:
        assert kwargs["model"] == "gemini-2.5-flash"
        return SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(text="Inspecting schema", function_call=None),
                            SimpleNamespace(
                                text=None,
                                function_call=SimpleNamespace(
                                    name="get_contract", args={"name": "openapi"}
                                ),
                            ),
                        ]
                    )
                )
            ],
            usage_metadata=SimpleNamespace(
                prompt_token_count=2000,
                cached_content_token_count=1500,
                candidates_token_count=60,
            ),
        )

    fake_client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=_fake_gen))
    )
    transport = GoogleGenAITransport(_fake_vault(), client_factory=lambda **_: fake_client)
    cfg = default_models_config().providers["gemini_paid"]
    resp = await transport.complete(
        "gemini_paid",
        cfg,
        "gemini-2.5-flash",
        GatewayRequest(
            role="lead",
            messages=[
                {"role": "system", "content": "You are a lead"},
                {"role": "user", "content": "Check OpenAPI"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_contract",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        ),
    )
    assert resp.tokens_in == 2000
    assert resp.tokens_cached == 1500
    assert resp.tokens_out == 60
    assert resp.tool_calls[0].name == "get_contract"
    assert resp.tool_calls[0].args == {"name": "openapi"}


@pytest.mark.asyncio
async def test_anthropic_transport_sets_cache_control_and_parses_tool_use() -> None:
    async def _fake_create(**kwargs: Any) -> Any:
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Reviewing diff"),
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_1",
                    name="write_note",
                    input={"note": "LGTM"},
                ),
            ],
            usage=SimpleNamespace(
                input_tokens=500,
                cache_read_input_tokens=400,
                output_tokens=30,
            ),
        )

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=_fake_create))
    transport = AnthropicTransport(
        _fake_vault(),
        client_factory=lambda **_: fake_client,  # type: ignore[arg-type,return-value]
    )
    cfg = default_models_config().providers["anthropic_paid"]
    resp = await transport.complete(
        "anthropic_paid",
        cfg,
        "claude-sonnet-4-5",
        GatewayRequest(
            role="reviewer",
            messages=[
                {"role": "system", "content": "Static system contract prefix"},
                {"role": "user", "content": "Review PR"},
            ],
        ),
    )
    assert resp.tokens_cached == 400
    assert resp.tool_calls[0].id == "toolu_1"
    assert resp.tool_calls[0].name == "write_note"


class _StubTransport:
    def __init__(self, *, fail_provider: str | None = None) -> None:
        self.name = "stub"
        self.fail_provider = fail_provider
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        self.calls.append((provider_id, model))
        if self.fail_provider == provider_id:
            raise RateLimitError("429 Too Many Requests")
        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=provider_cfg.transport,
            billing_mode=provider_cfg.billing_mode,
            content="ok",
            tokens_in=1_000_000,
            tokens_cached=500_000,
            tokens_out=100_000,
        )

    async def health_check(self, provider_id: str, provider_cfg: ProviderConfig) -> Any:
        return None


@pytest.mark.asyncio
async def test_gateway_rate_limit_fallback_vs_benchmark_pinning(tmp_path: Path) -> None:
    cfg = default_models_config()
    stub = _StubTransport(fail_provider="gemini_paid")
    transports = {
        TransportKind.GOOGLE_GENAI: stub,
        TransportKind.OPENAI_COMPAT: stub,
        TransportKind.ANTHROPIC: stub,
    }
    gateway = ModelGateway(
        cfg,
        cassettes=CassetteStore(tmp_path, miss_policy=MissPolicy.RECORD),
        transports=transports,  # type: ignore[arg-type]
    )
    req = GatewayRequest(role="implementer", messages=[{"role": "user", "content": "Build API"}])
    resp = await gateway.complete(req)
    assert resp.provider_id == "deepseek_paid"
    assert stub.calls == [
        ("gemini_paid", "gemini-2.5-flash-lite"),
        ("deepseek_paid", "deepseek-chat"),
    ]
    # Exact cost calculation: 500k uncached * 0.27 + 500k cached * 0.07 + 100k out * 1.10
    assert resp.cost_usd == pytest.approx(0.135 + 0.035 + 0.11, abs=1e-6)

    # Next identical call replays at $0 from cassette
    replay = await gateway.complete(req)
    assert replay.replayed is True
    assert replay.cost_usd == 0.0

    # Benchmark pinning disables cross-provider fallbacks
    pinned_cfg = ModelsConfig.model_validate(cfg.model_dump(by_alias=True))
    pinned_cfg.policies.benchmark_pin_models = True
    pinned_gateway = ModelGateway(
        pinned_cfg,
        cassettes=CassetteStore(tmp_path / "empty", miss_policy=MissPolicy.PASSTHROUGH),
        transports=transports,  # type: ignore[arg-type]
    )
    with pytest.raises(RateLimitError):
        await pinned_gateway.complete(req)


@pytest.mark.asyncio
async def test_gateway_enforces_free_tier_diversity_secret_scan_and_kill_switch() -> None:
    cfg = default_models_config()
    cfg.assignments.roles["implementer"] = "@free_coder"
    cfg.profiles["@free_coder"] = cfg.profiles["@coder"].model_copy(
        update={"provider": "gemini_free"}
    )
    stub = _StubTransport()
    transports = {
        TransportKind.GOOGLE_GENAI: stub,
        TransportKind.OPENAI_COMPAT: stub,
        TransportKind.ANTHROPIC: stub,
    }

    gateway = ModelGateway(
        cfg,
        data_policy=DataPolicyConfig(allow_free_tier=False),
        transports=transports,  # type: ignore[arg-type]
    )
    with pytest.raises(GatewayPolicyError, match="allow_free_tier=false"):
        await gateway.complete(
            GatewayRequest(role="implementer", messages=[{"role": "user", "content": "hi"}])
        )

    # Secret scanner blocks leaked keys before transport call
    cfg_clean = default_models_config()
    budget = RunBudgetState(BudgetConfig(per_run_max_llm_calls=1))
    gw_clean = ModelGateway(
        cfg_clean,
        run_budget=budget,
        transports=transports,  # type: ignore[arg-type]
    )
    leaked_secret = "sk-" + "ant-api03-1234567890abcdef1234567890"
    with pytest.raises(SecretLeakError):
        await gw_clean.complete(
            GatewayRequest(
                role="implementer",
                messages=[{"role": "user", "content": f"token={leaked_secret}"}],
            )
        )

    # Verifier diversity rejects same-family verifier
    with pytest.raises(GatewayPolicyError, match="cross-family diversity"):
        await gw_clean.complete(
            GatewayRequest(
                role="test_writer",
                peer_implementer_family="xai",
                messages=[{"role": "user", "content": "Write tests"}],
            )
        )

    # First call succeeds (1/1 calls), second call hits per_run_max_llm_calls kill-switch
    await gw_clean.complete(
        GatewayRequest(role="implementer", attempt=2, messages=[{"role": "user", "content": "ok"}])
    )
    # attempt=2 upgrades @coder -> @coder_plus
    assert stub.calls[-1] == ("gemini_paid", "gemini-2.5-flash")
    with pytest.raises(BudgetExceededError, match="max LLM calls"):
        await gw_clean.complete(
            GatewayRequest(role="implementer", messages=[{"role": "user", "content": "again"}])
        )


@pytest.mark.asyncio
async def test_multi_turn_tool_conversion_for_gemini_and_anthropic() -> None:
    multi_turn_msgs = [
        {"role": "system", "content": "Sys prompt"},
        {"role": "user", "content": "Inspect files"},
        {
            "role": "assistant",
            "content": "Checking both files.",
            "tool_calls": [
                {
                    "id": "tc_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                },
                {"id": "tc_2", "name": "read_file", "args": {"path": "b.py"}},
            ],
        },
        {"role": "tool", "tool_call_id": "tc_1", "name": "read_file", "content": "content a"},
        {"role": "tool", "tool_call_id": "tc_2", "name": "read_file", "content": "content b"},
    ]

    # Gemini multi-turn check
    captured_gem: dict[str, Any] = {}

    async def _fake_gem(**kwargs: Any) -> Any:
        captured_gem.update(kwargs)
        return SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(text="Done", function_call=None)]
                    )
                )
            ],
            usage_metadata=SimpleNamespace(
                prompt_token_count=100, cached_content_token_count=50, candidates_token_count=10
            ),
        )

    gem_transport = GoogleGenAITransport(
        _fake_vault(),
        client_factory=lambda **_: SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=_fake_gem))
        ),
    )
    await gem_transport.complete(
        "gemini_paid",
        default_models_config().providers["gemini_paid"],
        "gemini-2.5-flash",
        GatewayRequest(role="implementer", messages=multi_turn_msgs),
    )
    gem_contents = captured_gem["contents"]
    assert gem_contents[1]["role"] == "model"
    assert gem_contents[1]["parts"][1]["function_call"] == {
        "name": "read_file",
        "args": {"path": "a.py"},
    }
    assert gem_contents[1]["parts"][2]["function_call"] == {
        "name": "read_file",
        "args": {"path": "b.py"},
    }
    # Consecutive tool responses merged into one user turn
    assert gem_contents[2]["role"] == "user"
    assert len(gem_contents[2]["parts"]) == 2
    assert gem_contents[2]["parts"][0]["function_response"]["response"] == {"output": "content a"}

    # Anthropic multi-turn + token accounting check
    captured_ant: dict[str, Any] = {}

    async def _fake_ant(**kwargs: Any) -> Any:
        captured_ant.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="All good")],
            usage=SimpleNamespace(
                input_tokens=100_000,
                cache_read_input_tokens=400_000,
                cache_creation_input_tokens=200_000,
                output_tokens=50_000,
            ),
        )

    ant_closed = False

    async def _ant_close() -> None:
        nonlocal ant_closed
        ant_closed = True

    ant_cfg = default_models_config()
    ant_cfg.profiles["@reviewer_ant"] = ant_cfg.profiles["@verifier"].model_copy(
        update={
            "provider": "anthropic_paid",
            "model": "claude-sonnet-4-5",
            "family": "anthropic",
            "price": TokenPrice(**{"in": 3.0, "cached_in": 0.30, "out": 15.0}),
        }
    )
    ant_cfg.assignments.roles["reviewer"] = "@reviewer_ant"
    ant_transport = AnthropicTransport(
        _fake_vault(),
        client_factory=lambda **_: SimpleNamespace(  # type: ignore[arg-type,return-value]
            messages=SimpleNamespace(create=_fake_ant), close=_ant_close
        ),
    )
    gw = ModelGateway(
        ant_cfg,
        vault=_fake_vault(),
        transports={TransportKind.ANTHROPIC: ant_transport},  # type: ignore[dict-item]
    )
    resp = await gw.complete(GatewayRequest(role="reviewer", messages=multi_turn_msgs))
    ant_msgs = captured_ant["messages"]
    assert ant_msgs[1]["role"] == "assistant"
    assert ant_msgs[1]["content"][1]["type"] == "tool_use"
    assert ant_msgs[2]["role"] == "user"
    assert len(ant_msgs[2]["content"]) == 2
    assert ant_msgs[2]["content"][0]["type"] == "tool_result"

    # Cost: 100k uncached ($0.30) + 400k read ($0.12) + 200k write ($0.75) + 50k out ($0.75)
    assert resp.tokens_in == 700_000
    assert resp.tokens_cached == 400_000
    assert resp.tokens_cache_write == 200_000
    assert resp.cost_usd == pytest.approx(0.30 + 0.12 + 0.75 + 0.75, abs=1e-6)
    await gw.aclose()
    assert ant_closed is True


@pytest.mark.asyncio
async def test_openai_client_cache_gpt5_max_completion_tokens_and_bad_json() -> None:
    factory_calls = 0
    captured_kwargs: list[dict[str, Any]] = []

    async def _fake_create(**kwargs: Any) -> Any:
        captured_kwargs.append(kwargs)
        bad_json = len(captured_kwargs) == 2
        args_str = "{bad json" if bad_json else "{}"
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="tc_1",
                                function=SimpleNamespace(name="noop", arguments=args_str),
                            )
                        ],
                    )
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None
            ),
        )

    def _factory(**_: Any) -> Any:
        nonlocal factory_calls
        factory_calls += 1
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=_fake_create))
        )

    transport = OpenAICompatibleTransport(_fake_vault(), client_factory=_factory)
    cfg = default_models_config().providers["openai_paid"]

    await transport.complete(
        "openai_paid",
        cfg,
        "gpt-5",
        GatewayRequest(
            role="orchestrator", max_tokens=2048, messages=[{"role": "user", "content": "plan"}]
        ),
    )
    assert factory_calls == 1
    assert "max_completion_tokens" in captured_kwargs[0]
    assert "max_tokens" not in captured_kwargs[0]

    with pytest.raises(TransportError, match="Malformed JSON tool arguments"):
        await transport.complete(
            "openai_paid",
            cfg,
            "gpt-5",
            GatewayRequest(role="orchestrator", messages=[{"role": "user", "content": "again"}]),
        )
    # Client instance was cached & reused
    assert factory_calls == 1


@pytest.mark.asyncio
async def test_partitioned_sub_budgets_and_warn_threshold() -> None:
    class _FixedCostStub(_StubTransport):
        async def complete(
            self,
            provider_id: str,
            provider_cfg: ProviderConfig,
            model: str,
            request: GatewayRequest,
        ) -> GatewayResponse:
            return GatewayResponse(
                provider_id=provider_id,
                model=model,
                transport=provider_cfg.transport,
                billing_mode=provider_cfg.billing_mode,
                content="ok",
                tokens_in=500_000,
                tokens_cached=0,
                tokens_out=200_000,
            )

    cfg = default_models_config()
    stub = _FixedCostStub()
    budgets = BudgetConfig(
        per_run_usd=10.0,
        per_task={"implement_usd": 0.50, "review_usd": 0.15},  # type: ignore[arg-type]
        per_feature={"blackbox_tests_usd": 0.30},  # type: ignore[arg-type]
        per_domain_lead_usd=0.60,
        warn_at=0.80,
    )
    run_budget = RunBudgetState(budgets)
    gw = ModelGateway(
        cfg,
        run_budget=run_budget,
        transports={
            TransportKind.GOOGLE_GENAI: stub,
            TransportKind.OPENAI_COMPAT: stub,
            TransportKind.ANTHROPIC: stub,
        },  # type: ignore[arg-type]
    )
    # @coder (@ 0.25 in, 1.50 out): 500k in ($0.125) + 200k out ($0.300) = $0.425 per call
    # $0.425 >= 0.80 * $0.50 -> warning triggered on call 1; call 2 stays < cap before call,
    # and call 3 is blocked by task_implement limit
    req_t1 = GatewayRequest(
        role="implementer", task_id="t_1", messages=[{"role": "user", "content": "step 1"}]
    )
    await gw.complete(req_t1)
    assert any("task_implement:t_1" in w for w in run_budget.warnings)
    await gw.complete(req_t1)
    with pytest.raises(BudgetExceededError, match="task_implement:t_1"):
        await gw.complete(req_t1)

    # Separate feature / review / lead buckets remain unblocked by t_1 exhaustion
    rev_req = GatewayRequest(
        role="reviewer", task_id="t_1", messages=[{"role": "user", "content": "review"}]
    )
    await gw.complete(rev_req)  # costs 1.125 (>= 0.15 review cap)
    with pytest.raises(BudgetExceededError, match="task_review:t_1"):
        await gw.complete(rev_req)


@pytest.mark.asyncio
async def test_gateway_key_ref_override_passes_bound_key_to_transport() -> None:
    seen_key_refs: list[str | None] = []

    class _CaptureKeyStub:
        name = "capture"

        async def complete(
            self,
            provider_id: str,
            provider_cfg: ProviderConfig,
            model: str,
            request: GatewayRequest,
        ) -> GatewayResponse:
            seen_key_refs.append(provider_cfg.key_ref)
            return GatewayResponse(
                provider_id=provider_id,
                model=model,
                transport=provider_cfg.transport,
                billing_mode=provider_cfg.billing_mode,
                content="ok",
                tokens_in=10,
                tokens_cached=0,
                tokens_out=5,
            )

        async def health_check(self, provider_id: str, provider_cfg: ProviderConfig) -> Any:
            return None

    cfg = default_models_config()
    stub = _CaptureKeyStub()
    gw = ModelGateway(
        cfg,
        transports={
            TransportKind.GOOGLE_GENAI: stub,
            TransportKind.OPENAI_COMPAT: stub,
            TransportKind.ANTHROPIC: stub,
        },  # type: ignore[arg-type]
    )
    await gw.complete(
        GatewayRequest(
            role="implementer",
            messages=[{"role": "user", "content": "use custom key"}],
            key_ref_override="vault:var_api_key_1",
        )
    )
    assert seen_key_refs == ["vault:var_api_key_1"]
    assert cfg.providers["gemini_paid"].key_ref == "vault:gemini"


@pytest.mark.asyncio
async def test_openai_codex_cli_transport_opt_in_and_no_tools_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Disabled by default -> GatewayPolicyError
    cfg = default_models_config()
    assert cfg.policies.allow_openai_subscription_cli is False
    stub_cli = _StubTransport()
    gw_disabled = ModelGateway(
        cfg,
        transports={
            TransportKind.GOOGLE_GENAI: stub_cli,
            TransportKind.OPENAI_COMPAT: stub_cli,
            TransportKind.ANTHROPIC: stub_cli,
            TransportKind.OPENAI_CODEX_CLI: stub_cli,
        },  # type: ignore[arg-type]
    )
    with pytest.raises(GatewayPolicyError, match="disabled"):
        await gw_disabled.complete(
            GatewayRequest(
                role="orchestrator",
                messages=[{"role": "user", "content": "hi"}],
                provider_override="openai_subscription_cli",
                model_override="gpt-5",
            )
        )

    # Even when enabled, tools are strictly rejected (A7-2 single-shot non-tool invariant)
    cfg.policies.allow_openai_subscription_cli = True
    gw_enabled = ModelGateway(
        cfg,
        transports={
            TransportKind.GOOGLE_GENAI: stub_cli,
            TransportKind.OPENAI_COMPAT: stub_cli,
            TransportKind.ANTHROPIC: stub_cli,
            TransportKind.OPENAI_CODEX_CLI: stub_cli,
        },  # type: ignore[arg-type]
    )
    with pytest.raises(GatewayPolicyError, match="does not support native tool calling"):
        await gw_enabled.complete(
            GatewayRequest(
                role="orchestrator",
                messages=[{"role": "user", "content": "use tools"}],
                tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
                provider_override="openai_subscription_cli",
                model_override="gpt-5",
            )
        )

    # Direct OpenAICodexCliTransport also refuses tool requests & runs `codex exec` via subprocess
    transport = OpenAICodexCliTransport()
    prov_cfg = cfg.providers["openai_subscription_cli"]
    with pytest.raises(TransportError, match="single-shot non-tool only"):
        await transport.complete(
            "openai_subscription_cli",
            prov_cfg,
            "gpt-5",
            GatewayRequest(
                role="orchestrator",
                messages=[{"role": "user", "content": "test"}],
                tools=[{"type": "function", "function": {"name": "noop", "parameters": {}}}],
            ),
        )

    captured_cmd: list[str] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self, stdin_bytes: bytes | None = None) -> tuple[bytes, bytes]:
            assert stdin_bytes is not None
            assert b"Hello subscription" in stdin_bytes
            return b"OK from codex", b""

    async def _fake_exec(*cmd: str, **_: Any) -> _FakeProc:
        captured_cmd.extend(cmd)
        return _FakeProc()

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

    resp = await transport.complete(
        "openai_subscription_cli",
        prov_cfg,
        "gpt-5-codex",
        GatewayRequest(
            role="orchestrator",
            messages=[{"role": "user", "content": "Hello subscription"}],
        ),
    )
    assert resp.content == "OK from codex"
    assert resp.transport == TransportKind.OPENAI_CODEX_CLI
    assert captured_cmd[:6] == [
        "/usr/local/bin/codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-m",
    ]
    assert captured_cmd[6] == "gpt-5-codex"
    assert captured_cmd[7] == "-"


@pytest.mark.asyncio
async def test_gateway_provider_and_model_override_routes_directly() -> None:
    cfg = default_models_config()
    stub = _StubTransport()
    gw = ModelGateway(
        cfg,
        transports={
            TransportKind.GOOGLE_GENAI: stub,
            TransportKind.OPENAI_COMPAT: stub,
            TransportKind.ANTHROPIC: stub,
            TransportKind.OPENAI_CODEX_CLI: stub,
        },  # type: ignore[arg-type]
    )
    resp = await gw.complete(
        GatewayRequest(
            role="orchestrator",
            messages=[{"role": "user", "content": "hello"}],
            provider_override="openai_paid",
            model_override="gpt-4.1-mini",
            key_ref_override="vault:var_openai_key_1",
        )
    )
    assert resp.provider_id == "openai_paid"
    assert resp.model == "gpt-4.1-mini"
    assert stub.calls == [("openai_paid", "gpt-4.1-mini")]
