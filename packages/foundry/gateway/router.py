"""ModelGateway: assignment resolution, policy checks, kill-switches, and transport dispatch."""

from __future__ import annotations

import inspect
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from foundry.config.schemas import (
    ApiKeysRegistryConfig,
    BillingEnforcement,
    BillingMode,
    BudgetConfig,
    DataPolicyConfig,
    DataTier,
    ModelProfile,
    ModelsConfig,
    ProviderConfig,
    TransportKind,
)
from foundry.gateway.cassette import CassetteMissError, CassetteStore, MissPolicy
from foundry.gateway.normalizer import PromptNormalizer, compact_tool_history
from foundry.gateway.protocol import (
    GatewayRequest,
    GatewayResponse,
    ModelTransport,
    RateLimitError,
    TransportError,
    TransportHealth,
)
from foundry.gateway.rate_limit import RateLimitRouter
from foundry.gateway.transports import (
    AnthropicTransport,
    GoogleGenAITransport,
    OpenAICodexCliTransport,
    OpenAICompatibleTransport,
)
from foundry.security.scanner import SecretScanner
from foundry.security.vault import CredentialVault


class GatewayPolicyError(Exception):
    """Raised when a model selection violates data policy, capabilities, or diversity rules."""


class BudgetExceededError(Exception):
    """Raised when a run exceeds USD caps, max LLM calls, or wall-clock kill-switches."""


@dataclass
class RunBudgetState:
    """In-memory run ledger tracking exact USD spend, partitioned sub-budgets, and kill-switch."""

    budgets: BudgetConfig
    started_monotonic: float = field(default_factory=time.monotonic)
    spent_usd: float = 0.0
    spent_shadow_usd: float = 0.0
    llm_calls: int = 0
    calls_log: list[GatewayResponse] = field(default_factory=list)
    task_implement_usd: dict[str, float] = field(default_factory=dict)
    task_review_usd: dict[str, float] = field(default_factory=dict)
    feature_tests_usd: dict[str, float] = field(default_factory=dict)
    domain_lead_usd: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    _warned_keys: set[str] = field(default_factory=set)

    def _tracked_delta(self, response: GatewayResponse) -> float:
        return (
            response.est_value_usd
            if self.budgets.billing_enforcement == BillingEnforcement.SHADOW_USD
            else response.cost_usd
        )

    def _sub_budget_bucket(
        self,
        request: GatewayRequest | None,
    ) -> tuple[str, dict[str, float], str, float] | None:
        if request is None:
            return None
        if request.role in {"implementer", "integration"} and request.task_id:
            return (
                f"task_implement:{request.task_id}",
                self.task_implement_usd,
                request.task_id,
                self.budgets.per_task.implement_usd,
            )
        if request.role == "reviewer" and request.task_id:
            return (
                f"task_review:{request.task_id}",
                self.task_review_usd,
                request.task_id,
                self.budgets.per_task.review_usd,
            )
        if request.role == "test_writer" and request.feature:
            return (
                f"feature_tests:{request.feature}",
                self.feature_tests_usd,
                request.feature,
                self.budgets.per_feature.blackbox_tests_usd,
            )
        if request.role == "lead" and request.domain:
            return (
                f"domain_lead:{request.domain}",
                self.domain_lead_usd,
                request.domain,
                self.budgets.per_domain_lead_usd,
            )
        return None

    def _maybe_warn(self, key: str, spent: float, cap: float) -> None:
        if cap <= 0 or key in self._warned_keys:
            return
        if spent >= (cap * self.budgets.warn_at):
            self._warned_keys.add(key)
            self.warnings.append(
                f"Budget threshold {int(self.budgets.warn_at * 100)}% reached for {key}: "
                f"${spent:.4f} / ${cap:.2f}"
            )

    def check_can_call(self, request: GatewayRequest | None = None) -> None:
        elapsed_minutes = (time.monotonic() - self.started_monotonic) / 60.0
        if elapsed_minutes > self.budgets.per_run_wall_clock_minutes:
            raise BudgetExceededError(
                f"Run wall-clock limit ({self.budgets.per_run_wall_clock_minutes} min) exceeded"
            )
        if self.llm_calls >= self.budgets.per_run_max_llm_calls:
            raise BudgetExceededError(
                f"Run max LLM calls cap ({self.budgets.per_run_max_llm_calls}) reached"
            )
        tracked_spend = (
            self.spent_shadow_usd
            if self.budgets.billing_enforcement == BillingEnforcement.SHADOW_USD
            else self.spent_usd
        )
        if tracked_spend >= self.budgets.per_run_usd:
            raise BudgetExceededError(
                f"Run budget cap (${self.budgets.per_run_usd:.2f}) reached "
                f"(spent=${tracked_spend:.4f})"
            )

        bucket = self._sub_budget_bucket(request)
        if bucket is not None:
            label, store, key, cap = bucket
            sub_spent = store.get(key, 0.0)
            if sub_spent >= cap:
                raise BudgetExceededError(
                    f"Partitioned budget cap reached for {label}: ${sub_spent:.4f} >= ${cap:.2f}"
                )

    def record(
        self,
        response: GatewayResponse,
        request: GatewayRequest | None = None,
    ) -> None:
        self.llm_calls += 1
        self.spent_usd += response.cost_usd
        self.spent_shadow_usd += response.est_value_usd
        self.calls_log.append(response)

        tracked_total = (
            self.spent_shadow_usd
            if self.budgets.billing_enforcement == BillingEnforcement.SHADOW_USD
            else self.spent_usd
        )
        self._maybe_warn("run", tracked_total, self.budgets.per_run_usd)

        delta = self._tracked_delta(response)
        bucket = self._sub_budget_bucket(request)
        if bucket is not None:
            label, store, key, cap = bucket
            store[key] = round(store.get(key, 0.0) + delta, 6)
            self._maybe_warn(label, store[key], cap)


def _compute_token_costs(
    *,
    profile: ModelProfile,
    provider_cfg: ProviderConfig,
    tokens_in: int,
    tokens_cached: int,
    tokens_cache_write: int = 0,
    tokens_out: int,
) -> tuple[float, float]:
    uncached_in = max(0, tokens_in - tokens_cached - tokens_cache_write)
    cache_write_price = 1.25 * profile.price.input_per_m
    est_value_usd = (
        (uncached_in * profile.price.input_per_m)
        + (tokens_cached * profile.price.cached_input_per_m)
        + (tokens_cache_write * cache_write_price)
        + (tokens_out * profile.price.output_per_m)
    ) / 1_000_000.0
    cost_usd = est_value_usd if provider_cfg.billing_mode == BillingMode.PAID_API else 0.0
    return round(cost_usd, 6), round(est_value_usd, 6)


class ModelGateway:
    """Central entry point for every LLM call across Foundry agents and services."""

    def __init__(
        self,
        models_cfg: ModelsConfig,
        *,
        vault: CredentialVault | None = None,
        data_policy: DataPolicyConfig | None = None,
        scanner: SecretScanner | None = None,
        normalizer: PromptNormalizer | None = None,
        cassettes: CassetteStore | None = None,
        rate_router: RateLimitRouter | None = None,
        transports: Mapping[TransportKind, ModelTransport] | None = None,
        run_budget: RunBudgetState | None = None,
    ) -> None:
        self.models_cfg = models_cfg
        self.vault = vault or CredentialVault()
        self.data_policy = data_policy or DataPolicyConfig()
        self.scanner = scanner or SecretScanner()
        self.normalizer = normalizer or PromptNormalizer()
        self.cassettes = cassettes
        self.rate_router = rate_router or RateLimitRouter()
        self.run_budget = run_budget
        self.transports: dict[TransportKind, ModelTransport] = (
            dict(transports)
            if transports is not None
            else {
                TransportKind.OPENAI_COMPAT: OpenAICompatibleTransport(self.vault),
                TransportKind.GOOGLE_GENAI: GoogleGenAITransport(self.vault),
                TransportKind.ANTHROPIC: AnthropicTransport(self.vault),
                TransportKind.OPENAI_CODEX_CLI: OpenAICodexCliTransport(),
            }
        )

    def resolve_profile_name(self, request: GatewayRequest) -> str:
        assignments = self.models_cfg.assignments
        profile_name: str | None = None
        if request.task_type and request.task_type in assignments.task_types:
            profile_name = assignments.task_types[request.task_type]
        elif request.agent_instance and request.agent_instance in assignments.instances:
            profile_name = assignments.instances[request.agent_instance]
        elif request.role in assignments.roles:
            profile_name = assignments.roles[request.role]
        elif request.domain and request.domain in assignments.domains:
            profile_name = assignments.domains[request.domain]
        else:
            profile_name = assignments.project_default or assignments.global_default

        if (
            request.role == "implementer"
            and profile_name == "@coder"
            and request.attempt >= self.models_cfg.policies.upgrade_on_attempt
            and "@coder_plus" in self.models_cfg.profiles
        ):
            profile_name = "@coder_plus"

        if profile_name not in self.models_cfg.profiles:
            raise GatewayPolicyError(f"Resolved profile '{profile_name}' does not exist")
        return profile_name

    def _validate_candidate(
        self,
        profile_name: str,
        request: GatewayRequest,
    ) -> tuple[ModelProfile, ProviderConfig]:
        profile = self.models_cfg.profiles[profile_name]
        effective_provider_id = request.provider_override or profile.provider
        if effective_provider_id not in self.models_cfg.providers:
            raise GatewayPolicyError(
                f"Resolved provider '{effective_provider_id}' does not exist in models config"
            )
        provider_cfg = self.models_cfg.providers[effective_provider_id]
        if request.provider_override or request.model_override:
            profile = profile.model_copy(
                update={
                    "provider": effective_provider_id,
                    "model": request.model_override or profile.model,
                    "family": provider_cfg.family or profile.family,
                }
            )

        if (
            provider_cfg.transport == TransportKind.OPENAI_CODEX_CLI
            and not self.models_cfg.policies.allow_openai_subscription_cli
        ):
            raise GatewayPolicyError(
                "OpenAI Subscription (Codex CLI) transport is disabled. "
                "Enable opt-in single-shot subscription mode in API Keys first."
            )

        is_free = (
            provider_cfg.billing_mode == BillingMode.FREE_TIER
            or provider_cfg.data_tier == DataTier.FREE_TRAINING_ALLOWED
        )
        if is_free and not self.data_policy.allow_free_tier:
            raise GatewayPolicyError(
                f"Provider '{profile.provider}' uses free-tier data policy, "
                "which is disabled by project data_policy.allow_free_tier=false"
            )

        if request.tools and not provider_cfg.supports_tool_calling:
            raise GatewayPolicyError(
                f"Provider '{profile.provider}' does not support native tool calling"
            )

        diversity_policy = self.models_cfg.policies.require_verifier_family_diversity
        enforce_diversity = diversity_policy in (True, "true")
        candidate_family = profile.family or provider_cfg.family
        if (
            enforce_diversity
            and request.role in {"test_writer", "reviewer"}
            and request.peer_implementer_family
            and candidate_family
            and candidate_family == request.peer_implementer_family
        ):
            raise GatewayPolicyError(
                f"Verifier '{profile_name}' shares model family '{candidate_family}' "
                f"with implementer; cross-family diversity is required"
            )
        return profile, provider_cfg

    async def complete(self, request: GatewayRequest) -> GatewayResponse:
        if self.run_budget is not None:
            self.run_budget.check_can_call(request)

        # 1. Scan outgoing messages and tools for secrets before normalization or network I/O
        self.scanner.assert_clean(request.messages, request.tools)

        # 2. Compact older tool outputs to keep native prefix caches warm
        compacted_messages = compact_tool_history(request.messages, keep_recent_steps=6)
        primary_profile = self.resolve_profile_name(request)
        # Validate primary assignment strictly before considering fallbacks
        explicit_override = bool(request.provider_override or request.model_override)
        primary_resolved_prof, primary_resolved_prov = self._validate_candidate(
            primary_profile, request
        )
        if explicit_override:
            compliant = [(primary_profile, primary_resolved_prof, primary_resolved_prov)]
        else:
            candidates = self.rate_router.candidate_profiles(primary_profile, self.models_cfg)
            compliant = []
            for name in candidates:
                try:
                    profile, provider_cfg = self._validate_candidate(name, request)
                    compliant.append((name, profile, provider_cfg))
                except GatewayPolicyError:
                    continue

        primary_name, primary_prof, _ = compliant[0]
        prompt_hash = self.normalizer.compute_hash(
            model=primary_prof.model,
            messages=compacted_messages,
            tools=request.tools,
        )

        # 3. Check normalized record/replay cassettes before live transport
        if self.cassettes is not None and self.cassettes.miss_policy != MissPolicy.PASSTHROUGH:
            replayed = self.cassettes.lookup(
                prompt_hash=prompt_hash,
                role=request.role,
                task_slug=request.task_slug,
                node=request.node,
                step_index=request.step_index,
            )
            if replayed is not None:
                if self.run_budget is not None:
                    self.run_budget.record(replayed, request)
                return replayed
            if self.cassettes.miss_policy == MissPolicy.STRICT:
                raise CassetteMissError(
                    f"Strict cassette miss for role={request.role}, "
                    f"model={primary_prof.model}, hash={prompt_hash[:12]}"
                )

        # 4. Dispatch to first-party SDK transport with rate-limit fallback (unless pinned)
        compacted_request = request.model_copy(update={"messages": compacted_messages})
        last_error: Exception | None = None
        for _, profile, provider_cfg in compliant:
            effective_provider_cfg = (
                provider_cfg.model_copy(update={"key_ref": request.key_ref_override})
                if request.key_ref_override
                else provider_cfg
            )
            transport = self.transports.get(effective_provider_cfg.transport)
            if transport is None:
                raise TransportError(
                    f"No transport registered for {effective_provider_cfg.transport}"
                )
            try:
                response = await transport.complete(
                    profile.provider,
                    effective_provider_cfg,
                    profile.model,
                    compacted_request,
                )
            except RateLimitError as exc:
                self.rate_router.mark_cooldown(profile.provider, provider_cfg.cooldown_seconds)
                last_error = exc
                if self.models_cfg.policies.benchmark_pin_models:
                    raise
                continue

            cost_usd, est_value_usd = _compute_token_costs(
                profile=profile,
                provider_cfg=provider_cfg,
                tokens_in=response.tokens_in,
                tokens_cached=response.tokens_cached,
                tokens_cache_write=response.tokens_cache_write,
                tokens_out=response.tokens_out,
            )
            finalized = response.model_copy(
                update={
                    "cost_usd": cost_usd,
                    "est_value_usd": est_value_usd,
                    "prompt_hash": prompt_hash,
                }
            )
            if self.cassettes is not None and self.cassettes.miss_policy == MissPolicy.RECORD:
                self.cassettes.save(
                    finalized,
                    prompt_hash=prompt_hash,
                    role=request.role,
                    task_slug=request.task_slug,
                    node=request.node,
                    step_index=request.step_index,
                )
            if self.run_budget is not None:
                self.run_budget.record(finalized, request)
            return finalized

        if last_error is not None:
            raise last_error
        raise TransportError("All candidate model transports failed")

    async def aclose(self) -> None:
        for transport in self.transports.values():
            close_fn = getattr(transport, "aclose", None)
            if callable(close_fn):
                res = close_fn()
                if inspect.isawaitable(res):
                    await res

    async def health_matrix(self) -> list[TransportHealth]:
        results: list[TransportHealth] = []
        for provider_id, cfg in self.models_cfg.providers.items():
            transport = self.transports[cfg.transport]
            results.append(await transport.health_check(provider_id, cfg))
        return results


def resolve_api_key_ref(
    api_key_var: str | None,
    registry: ApiKeysRegistryConfig | None,
) -> str | None:
    """Resolve an agent's named API key variable (e.g. `API_KEY_1`) to its vault key_ref."""
    if not api_key_var or registry is None:
        return None
    target = api_key_var.strip().upper()
    for entry in registry.keys:
        if entry.name == target:
            return entry.key_ref
    return None
