"""Independent Orchestrator Project Graph (`{run}:project`) with re-run-safe interrupts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, runtime_checkable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from foundry.agents.prompts.module2 import (
    build_architecture_messages,
    build_requirements_messages,
    parse_json_object,
)
from foundry.blackboard.repository import BlackboardRepository
from foundry.decisions.service import DecisionService, is_auto_resolved
from foundry.gateway.protocol import GatewayRequest
from foundry.gateway.router import ModelGateway


@runtime_checkable
class ProjectSupervisorHooks(Protocol):
    """Idempotent orchestration hooks invoked inside wait-barrier nodes without upward imports."""

    async def ensure_contract_planning(self, run_id: str, state: dict[str, Any]) -> int | None: ...

    async def ensure_wave1(self, run_id: str, state: dict[str, Any]) -> bool: ...

    async def ensure_wave2(self, run_id: str, state: dict[str, Any]) -> bool: ...


class ProjectState(TypedDict, total=False):
    """Checkpointed state for `{run}:project` Orchestrator thread."""

    run_id: str
    task_slug: str
    app_name: str
    spec: str
    req_revision: int
    arch_revision: int
    requirements_draft: dict[str, Any] | None
    requirements: dict[str, Any] | None
    architecture_draft: dict[str, Any] | None
    architecture: dict[str, Any] | None
    feedback: str | None
    contracts_version: int | None
    wave1_completed: bool
    wave2_completed: bool
    phase: str


@dataclass
class ProjectGraphDeps:
    """Runtime dependencies bound into the compiled Project Graph."""

    gateway: ModelGateway
    decisions: DecisionService
    blackboard: BlackboardRepository
    supervisor_hooks: ProjectSupervisorHooks


def build_project_graph(deps: ProjectGraphDeps, *, checkpointer: Any | None = None) -> Any:
    """Compile the Project Graph keeping LLM calls and `interrupt()` in separate nodes."""

    async def draft_requirements(state: ProjectState) -> dict[str, Any]:
        run_id = state["run_id"]
        rev = int(state.get("req_revision", 0))
        req = GatewayRequest(
            role="orchestrator",
            task_slug=state["task_slug"],
            node="draft_requirements",
            step_index=rev,
            messages=build_requirements_messages(state["spec"], feedback=state.get("feedback")),
        )
        resp = await deps.gateway.complete(req)
        await deps.blackboard.record_llm_call(
            run_id=run_id,
            graph_thread_id=f"{run_id}:project",
            node=req.node,
            provider=resp.provider_id,
            model=resp.model,
            profile="@planner",
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
        draft = parse_json_object(resp.content)
        return {"requirements_draft": draft, "phase": "planning"}

    async def approve_requirements(state: ProjectState, config: RunnableConfig) -> dict[str, Any]:
        tid = str((config.get("configurable") or {})["thread_id"])
        rev = int(state.get("req_revision", 0))
        draft = state.get("requirements_draft") or {}
        decision = await deps.decisions.get_or_create(
            run_id=state["run_id"],
            idempotency_key=f"{tid}:approve_requirements:{rev}",
            category="strategic",
            title=f"Approve requirements for {state['app_name']}",
            context=f"Summary: {draft.get('summary', '')}",
            options=["approve", "reject"],
            recommendation="approve",
            confidence=0.90,
            reversibility="reversible",
            graph_thread_id=tid,
        )
        if is_auto_resolved(decision):
            return {"requirements": draft, "feedback": None, "phase": "architecture"}

        answer = interrupt(
            {
                "decision_id": decision.id,
                "category": "strategic",
                "title": decision.title,
            }
        )
        if isinstance(answer, dict) and answer.get("action") == "approve":
            return {"requirements": draft, "feedback": None, "phase": "architecture"}

        feedback = (
            str(answer.get("feedback"))
            if isinstance(answer, dict) and answer.get("feedback")
            else "Revise requirements per reviewer feedback."
        )
        return {
            "requirements": None,
            "requirements_draft": None,
            "feedback": feedback,
            "req_revision": rev + 1,
        }

    def _after_approve_requirements(state: ProjectState) -> str:
        return "draft_architecture" if state.get("requirements") else "draft_requirements"

    async def draft_architecture(state: ProjectState) -> dict[str, Any]:
        run_id = state["run_id"]
        rev = int(state.get("arch_revision", 0))
        requirements = state.get("requirements") or {}
        req = GatewayRequest(
            role="orchestrator",
            task_slug=state["task_slug"],
            node="draft_architecture",
            step_index=rev,
            messages=build_architecture_messages(
                state["spec"],
                requirements,
                feedback=state.get("feedback"),
            ),
        )
        resp = await deps.gateway.complete(req)
        await deps.blackboard.record_llm_call(
            run_id=run_id,
            graph_thread_id=f"{run_id}:project",
            node=req.node,
            provider=resp.provider_id,
            model=resp.model,
            profile="@planner",
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
        draft = parse_json_object(resp.content)
        return {"architecture_draft": draft}

    async def approve_architecture(state: ProjectState, config: RunnableConfig) -> dict[str, Any]:
        tid = str((config.get("configurable") or {})["thread_id"])
        rev = int(state.get("arch_revision", 0))
        draft = state.get("architecture_draft") or {}
        decision = await deps.decisions.get_or_create(
            run_id=state["run_id"],
            idempotency_key=f"{tid}:approve_architecture:{rev}",
            category="strategic",
            title=f"Approve architecture for {state['app_name']}",
            context=f"Stack pack: {draft.get('stack_pack', 'fastapi-react-postgres')}",
            options=["approve", "reject"],
            recommendation="approve",
            confidence=0.90,
            reversibility="reversible",
            graph_thread_id=tid,
        )
        if is_auto_resolved(decision):
            return {"architecture": draft, "feedback": None, "phase": "contracts"}

        answer = interrupt(
            {
                "decision_id": decision.id,
                "category": "strategic",
                "title": decision.title,
            }
        )
        if isinstance(answer, dict) and answer.get("action") == "approve":
            return {"architecture": draft, "feedback": None, "phase": "contracts"}

        feedback = (
            str(answer.get("feedback"))
            if isinstance(answer, dict) and answer.get("feedback")
            else "Revise architecture per reviewer feedback."
        )
        return {
            "architecture": None,
            "architecture_draft": None,
            "feedback": feedback,
            "arch_revision": rev + 1,
        }

    def _after_approve_architecture(state: ProjectState) -> str:
        return "plan_contracts" if state.get("architecture") else "draft_architecture"

    async def plan_contracts(state: ProjectState) -> dict[str, Any]:
        run_id = state["run_id"]
        await deps.blackboard.update_run_phase(run_id, "contracts")
        ver = await deps.supervisor_hooks.ensure_contract_planning(run_id, dict(state))
        if ver is None:
            event = interrupt({"wait_for": "contracts.locked", "run_id": run_id})
            ver = int(event.get("version", 1)) if isinstance(event, dict) else 1
        return {"contracts_version": ver, "phase": "wave1"}

    async def wave1_foundation(state: ProjectState) -> dict[str, Any]:
        run_id = state["run_id"]
        await deps.blackboard.update_run_phase(run_id, "wave1")
        done = await deps.supervisor_hooks.ensure_wave1(run_id, dict(state))
        if not done:
            interrupt({"wait_for": "wave.completed", "wave": 1, "run_id": run_id})
        return {"wave1_completed": True, "phase": "wave2"}

    async def wave2_features(state: ProjectState) -> dict[str, Any]:
        run_id = state["run_id"]
        await deps.blackboard.update_run_phase(run_id, "wave2")
        done = await deps.supervisor_hooks.ensure_wave2(run_id, dict(state))
        if not done:
            interrupt({"wait_for": "wave.completed", "wave": 2, "run_id": run_id})
        await deps.blackboard.update_run_phase(run_id, "done", ended=True)
        return {"wave2_completed": True, "phase": "done"}

    graph = StateGraph(ProjectState)
    graph.add_node("draft_requirements", draft_requirements)
    graph.add_node("approve_requirements", approve_requirements)
    graph.add_node("draft_architecture", draft_architecture)
    graph.add_node("approve_architecture", approve_architecture)
    graph.add_node("plan_contracts", plan_contracts)
    graph.add_node("wave1_foundation", wave1_foundation)
    graph.add_node("wave2_features", wave2_features)

    graph.add_edge(START, "draft_requirements")
    graph.add_edge("draft_requirements", "approve_requirements")
    graph.add_conditional_edges(
        "approve_requirements",
        _after_approve_requirements,
        {"draft_architecture": "draft_architecture", "draft_requirements": "draft_requirements"},
    )
    graph.add_edge("draft_architecture", "approve_architecture")
    graph.add_conditional_edges(
        "approve_architecture",
        _after_approve_architecture,
        {"plan_contracts": "plan_contracts", "draft_architecture": "draft_architecture"},
    )
    graph.add_edge("plan_contracts", "wave1_foundation")
    graph.add_edge("wave1_foundation", "wave2_features")
    graph.add_edge("wave2_features", END)

    return graph.compile(checkpointer=checkpointer)
