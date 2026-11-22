"""Domain Lead graphs and staged contract planning (`Data -> Backend -> Frontend || Infra`)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from foundry.agents.prompts.module2 import (
    build_consistency_review_messages,
    build_lead_contract_messages,
    parse_json_object,
)
from foundry.blackboard.models import ContractRecord, ContractVersionRecord
from foundry.blackboard.repository import BlackboardRepository
from foundry.contracts.models import ContractValidationResult
from foundry.contracts.service import ContractService
from foundry.gateway.protocol import GatewayRequest
from foundry.gateway.router import ModelGateway
from foundry.verification.contract_checks import extract_pinned_pointers

_DOMAIN_CONTRACT_META: dict[str, tuple[str, str]] = {
    "data": ("db_schema", "db_schema"),
    "backend": ("api", "openapi"),
    "frontend": ("ui_components", "component"),
    "infra": ("deps", "deps"),
}


class LeadContractState(TypedDict, total=False):
    """State for an isolated single-domain Lead contract thread (`{run}:lead:{domain}`)."""

    run_id: str
    task_slug: str
    domain: str
    spec: str
    requirements: dict[str, Any]
    architecture: dict[str, Any]
    upstream_contracts: dict[str, Any]
    pinned_openapi_yaml: str | None
    pinned_testids_yaml: str | None
    pinned_pointers: list[str]
    agent_id: str
    draft_body: dict[str, Any]
    contract_id: str
    version: int


@dataclass
class LeadGraphDeps:
    """Dependencies bound to an independent Domain Lead LangGraph thread."""

    gateway: ModelGateway
    contracts: ContractService
    blackboard: BlackboardRepository


def build_lead_contract_graph(deps: LeadGraphDeps) -> Any:
    """Compile a re-run-safe single-domain Lead graph (`draft_contract` -> `persist_contract`)."""

    async def draft_contract(state: LeadContractState) -> dict[str, Any]:
        domain = state["domain"]
        messages = build_lead_contract_messages(
            domain=domain,
            spec=state["spec"],
            requirements=state["requirements"],
            architecture=state["architecture"],
            upstream_contracts=state.get("upstream_contracts", {}),
            pinned_openapi_yaml=state.get("pinned_openapi_yaml"),
            pinned_testids_yaml=state.get("pinned_testids_yaml"),
        )
        req = GatewayRequest(
            role="lead",
            domain=domain,
            task_slug=state["task_slug"],
            node=f"lead_{domain}_contract",
            step_index=0,
            messages=messages,
        )
        resp = await deps.gateway.complete(req)
        await deps.blackboard.record_llm_call(
            run_id=state["run_id"],
            agent_id=state.get("agent_id"),
            graph_thread_id=f"{state['run_id']}:lead:{domain}",
            node=req.node,
            provider=resp.provider_id,
            model=resp.model,
            profile="@lead",
            transport=resp.transport.value,
            billing_mode=resp.billing_mode.value,
            input_tokens=resp.tokens_in,
            cached_tokens=resp.tokens_cached,
            output_tokens=resp.tokens_out,
            cost_usd=resp.cost_usd,
            est_value_usd=resp.est_value_usd,
            latency_ms=resp.latency_ms,
            replayed=resp.replayed,
            prompt_hash=resp.prompt_hash,
        )
        draft_body = parse_json_object(resp.content)
        return {"draft_body": draft_body}

    async def persist_contract(state: LeadContractState) -> dict[str, Any]:
        domain = state["domain"]
        name, kind = _DOMAIN_CONTRACT_META[domain]
        ctr, ver = await deps.contracts.save_draft(
            run_id=state["run_id"],
            name=name,
            domain=domain,
            kind=kind,
            body=state["draft_body"],
            author_agent_id=state.get("agent_id"),
            pinned_pointers=state.get("pinned_pointers", []),
            change_kind="initial",
            change_summary=f"Staged {domain} v1 contract draft",
        )
        return {"contract_id": ctr.id, "version": ver.version}

    graph = StateGraph(LeadContractState)
    graph.add_node("draft_contract", draft_contract)
    graph.add_node("persist_contract", persist_contract)
    graph.add_edge(START, "draft_contract")
    graph.add_edge("draft_contract", "persist_contract")
    graph.add_edge("persist_contract", END)
    return graph.compile()


@dataclass
class StagedContractsOutcome:
    """Outputs of full staged contract planning + consistency review + v1 locking."""

    execution_order: list[str]
    contracts: dict[str, tuple[ContractRecord, ContractVersionRecord]]
    validation: ContractValidationResult
    consistency_review: dict[str, Any]


async def run_staged_contract_planning(
    *,
    run_id: str,
    task_slug: str,
    spec: str,
    requirements: dict[str, Any],
    architecture: dict[str, Any],
    gateway: ModelGateway,
    blackboard: BlackboardRepository,
    contracts_service: ContractService,
    pinned_openapi: dict[str, Any] | None = None,
    pinned_openapi_yaml: str | None = None,
    pinned_testids: tuple[str, ...] | list[str] = (),
    pinned_testids_yaml: str | None = None,
    checkpointer: Any | None = None,
) -> StagedContractsOutcome:
    """Execute Domain Leads in strict staged order: Data -> Backend -> Frontend || Infra."""
    _ = checkpointer  # Reserved for checkpointed long-lived lead resumption in M3
    deps = LeadGraphDeps(gateway=gateway, contracts=contracts_service, blackboard=blackboard)
    pinned_ptrs = extract_pinned_pointers(pinned_openapi) if pinned_openapi is not None else []
    execution_order: list[str] = []

    async def _run_domain(
        domain: str,
        *,
        upstream: dict[str, Any],
    ) -> dict[str, Any]:
        thread_id = f"{run_id}:lead:{domain}"
        agent = await blackboard.register_agent(
            run_id=run_id,
            role="lead",
            domain=domain,
            model_profile="@lead",
            graph_thread_id=thread_id,
        )
        graph = build_lead_contract_graph(deps)
        init_state: LeadContractState = {
            "run_id": run_id,
            "task_slug": task_slug,
            "domain": domain,
            "spec": spec,
            "requirements": requirements,
            "architecture": architecture,
            "upstream_contracts": upstream,
            "pinned_openapi_yaml": pinned_openapi_yaml if domain == "backend" else None,
            "pinned_testids_yaml": pinned_testids_yaml if domain == "frontend" else None,
            "pinned_pointers": pinned_ptrs if domain == "backend" else [],
            "agent_id": agent.id,
        }
        await blackboard.update_agent_status(agent.id, "running")
        out = await graph.ainvoke(init_state, {"configurable": {"thread_id": thread_id}})
        await blackboard.update_agent_status(agent.id, "idle")
        execution_order.append(domain)
        return dict(out)

    # Stage 1: Data Lead
    await _run_domain("data", upstream={})
    db_schema_body = await contracts_service.load_latest_body(run_id, "db_schema")

    # Stage 2: Backend Lead (consumes exact db_schema JSON)
    await _run_domain("backend", upstream={"db_schema": db_schema_body})
    api_body = await contracts_service.load_latest_body(run_id, "api")

    # Stage 3: Frontend Lead || Infra Lead concurrently on two independent threads
    upstream_stage3 = {"db_schema": db_schema_body, "api": api_body}
    await asyncio.gather(
        _run_domain("frontend", upstream=upstream_stage3),
        _run_domain("infra", upstream=upstream_stage3),
    )

    ui_body = await contracts_service.load_latest_body(run_id, "ui_components")
    deps_body = await contracts_service.load_latest_body(run_id, "deps")

    # Stage 4 + 5: Deterministic validation + single Orchestrator consistency review
    all_bodies = {
        "db_schema": db_schema_body,
        "api": api_body,
        "ui_components": ui_body,
        "deps": deps_body,
    }
    review_req = GatewayRequest(
        role="orchestrator",
        task_slug=task_slug,
        node="consistency_review",
        step_index=0,
        messages=build_consistency_review_messages(contracts=all_bodies),
    )
    review_resp = await gateway.complete(review_req)
    await blackboard.record_llm_call(
        run_id=run_id,
        graph_thread_id=f"{run_id}:project",
        node=review_req.node,
        provider=review_resp.provider_id,
        model=review_resp.model,
        profile="@planner",
        transport=review_resp.transport.value,
        billing_mode=review_resp.billing_mode.value,
        input_tokens=review_resp.tokens_in,
        cached_tokens=review_resp.tokens_cached,
        output_tokens=review_resp.tokens_out,
        cost_usd=review_resp.cost_usd,
        est_value_usd=review_resp.est_value_usd,
        latency_ms=review_resp.latency_ms,
        replayed=review_resp.replayed,
        prompt_hash=review_resp.prompt_hash,
    )
    review_json = parse_json_object(review_resp.content)
    if not review_json.get("consistent", True):
        issues = review_json.get("issues", [])
        raise ValueError(f"Orchestrator consistency review flagged issues: {issues}")

    # Stage 6: Validate and transition all 4 contracts -> 'locked'
    validation = await contracts_service.lock_all_v1(
        run_id=run_id,
        pinned_openapi=pinned_openapi,
        pinned_testids=pinned_testids,
    )

    loaded: dict[str, tuple[ContractRecord, ContractVersionRecord]] = {}
    for name in ("db_schema", "api", "ui_components", "deps"):
        ctr = await blackboard.get_contract(run_id, name)
        ver = await blackboard.get_latest_contract_version(ctr.id)
        loaded[name] = (ctr, ver)

    return StagedContractsOutcome(
        execution_order=execution_order,
        contracts=loaded,
        validation=validation,
        consistency_review=review_json,
    )
