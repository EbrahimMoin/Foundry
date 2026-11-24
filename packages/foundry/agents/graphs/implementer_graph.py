"""Independent per-task LangGraph Implementer worker (`{run}:task:{id}:{attempt}`, Sec 7.5)."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from foundry.agents.prompts.implementer import (
    IMPLEMENTER_TOOLS,
    build_implementer_messages,
    compact_old_tool_outputs,
)
from foundry.blackboard.models import NoteRecord, TaskRecord
from foundry.blackboard.repository import BlackboardRepository
from foundry.contracts.protocol import ContractChangeProtocol
from foundry.gateway.protocol import GatewayRequest
from foundry.gateway.router import BudgetExceededError, ModelGateway
from foundry.mcp_servers.git_server import GitMCPServer
from foundry.mcp_servers.sandbox_server import (
    CommandNotAllowedError,
    PackageApprovalRequiredError,
    SandboxMCPServer,
)
from foundry.sandbox.workspace_sync import sync_workspace_to_worktree, sync_worktree_to_workspace
from foundry.security.hardened_git import GitError, HardenedGit
from foundry.security.workspace_guard import (
    WorkspaceViolationError,
    normalize_repo_path,
    validate_workspace_tree,
)
from foundry.verification.gates import GateRunner
from foundry.verification.models import GateReport

_SKIP_LIST_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".foundry_tmp",
        ".hypothesis",
    }
)


def implementer_thread_id(run_id: str, task_id: str, attempt: int) -> str:
    """Canonical independent thread identifier (`{run}:task:{id}:{attempt}`, Sec 7.2)."""
    return f"{run_id}:task:{task_id}:{attempt}"


@runtime_checkable
class RecloneDbHook(Protocol):
    """Callback to re-clone `sbx_<task>` cleanly after an upstream schema barrier rebase."""

    async def reclone_after_schema(self, *, task_id: str, main_sha: str) -> None: ...


class ImplementerState(TypedDict, total=False):
    """State carried through an isolated Implementer thread."""

    run_id: str
    task_id: str
    task_slug: str
    attempt: int
    max_attempts: int
    step_index: int
    max_steps: int
    messages: list[dict[str, Any]]
    buffered_contract_diffs: list[dict[str, Any]]
    pending_schema_sha: str | None
    last_tool_calls: list[dict[str, Any]]
    done_requested: bool
    g1_passed: bool
    g2_passed: bool
    last_gate_summary: str
    committed_sha: str | None
    needs_clean_restart: bool
    final_status: str
    error: str | None


class ImplementerTaskResult(BaseModel):
    """Structured outcome returned by executing one Implementer attempt."""

    task_id: str
    status: Literal["verified_g2", "schema_rebase_restart", "failed", "budget_exceeded"]
    committed_sha: str | None = None
    new_base_sha: str | None = None
    g2_summary: str = ""
    error: str | None = None


@dataclass
class ImplementerGraphDeps:
    """Runtime dependencies bound into a single Implementer thread."""

    gateway: ModelGateway
    sandbox_mcp: SandboxMCPServer
    git_mcp: GitMCPServer
    gate_runner: GateRunner
    workspace_dir: Path
    worktree_dir: Path
    git: HardenedGit
    repo: BlackboardRepository
    task: TaskRecord
    write_set: list[str]
    contracts: dict[str, dict[str, Any]]
    notes: list[NoteRecord] = field(default_factory=list)
    contract_protocol: ContractChangeProtocol | None = None
    boundary_schema_ref: dict[str, str | None] | None = None
    reclone_hook: RecloneDbHook | None = None
    model_ids: set[str] = field(default_factory=set)
    last_g2_report: GateReport | None = None


def _safe_list_files(workspace_dir: Path, directory: str) -> list[str]:
    validate_workspace_tree(workspace_dir, ignore_git_dir=False)
    cleaned = directory.strip()
    if cleaned in {"", "."}:
        target_root = workspace_dir
    else:
        rel = normalize_repo_path(cleaned, allow_directory_slash=True).rstrip("/")
        target_root = workspace_dir / rel

    if not target_root.is_dir():
        raise FileNotFoundError(f"Directory does not exist: {directory}")

    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(target_root, topdown=True, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_LIST_DIRS)
        cur = Path(dirpath)
        for fname in sorted(filenames):
            results.append((cur / fname).relative_to(workspace_dir).as_posix())
    return results


def _format_assistant_msg(content: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def build_implementer_graph(
    deps: ImplementerGraphDeps,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Compile the multi-agent Implementer LangGraph (context -> act -> boundary -> commit)."""

    async def load_context(state: ImplementerState, config: RunnableConfig) -> dict[str, Any]:
        _ = config
        if state.get("messages"):
            return {}
        msgs = build_implementer_messages(
            task=deps.task,
            write_set=deps.write_set,
            contracts=deps.contracts,
            notes=deps.notes,
        )
        return {"messages": msgs}

    async def plan(state: ImplementerState, config: RunnableConfig) -> dict[str, Any]:
        _ = (state, config)
        return {"final_status": "running", "needs_clean_restart": False}

    async def act(state: ImplementerState, config: RunnableConfig) -> dict[str, Any]:
        _ = config
        compacted = compact_old_tool_outputs(
            state.get("messages", []),
            keep_recent_messages=6,
        )
        req = GatewayRequest(
            role="implementer",
            task_id=state["task_id"],
            task_slug=state["task_slug"],
            node="agent_step",
            step_index=state.get("step_index", 0),
            attempt=state.get("attempt", 1),
            messages=compacted,
            tools=IMPLEMENTER_TOOLS,
        )
        try:
            resp = await deps.gateway.complete(req)
        except BudgetExceededError as exc:
            return {
                "error": str(exc),
                "final_status": "budget_exceeded",
                "last_tool_calls": [],
            }

        deps.model_ids.add(f"{resp.provider_id}/{resp.model}")
        formatted_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.args, sort_keys=True),
                },
            }
            for tc in resp.tool_calls
        ]
        next_messages = [*compacted, _format_assistant_msg(resp.content, formatted_calls)]
        return {
            "messages": next_messages,
            "step_index": state.get("step_index", 0) + 1,
            "last_tool_calls": [
                {"id": tc.id, "name": tc.name, "args": tc.args} for tc in resp.tool_calls
            ],
            "done_requested": len(resp.tool_calls) == 0,
        }

    async def tool_node(state: ImplementerState, config: RunnableConfig) -> dict[str, Any]:
        _ = config
        messages = list(state.get("messages", []))
        done_requested = bool(state.get("done_requested", False))
        g1_passed = bool(state.get("g1_passed", False))
        last_gate_summary = str(state.get("last_gate_summary", ""))

        for call in state.get("last_tool_calls", []):
            call_id = str(call["id"])
            name = str(call["name"])
            args = call.get("args") or {}
            payload: dict[str, Any]

            try:
                if name == "read_file":
                    content = await asyncio.to_thread(
                        deps.sandbox_mcp.read_file, str(args.get("path", ""))
                    )
                    payload = {"ok": True, "content": content}
                elif name == "write_file":
                    written = await asyncio.to_thread(
                        deps.sandbox_mcp.write_file,
                        str(args.get("path", "")),
                        str(args.get("content", "")),
                    )
                    payload = {"ok": True, "path": written}
                elif name == "list_files":
                    files = await asyncio.to_thread(
                        _safe_list_files,
                        deps.workspace_dir,
                        str(args.get("directory", ".")),
                    )
                    payload = {"ok": True, "files": files}
                elif name == "run_command":
                    raw_cmd = args.get("cmd", [])
                    if not isinstance(raw_cmd, list):
                        raise ValueError("'cmd' must be a list of strings")
                    res = await deps.sandbox_mcp.run_command(
                        [str(x) for x in raw_cmd],
                        heavy=bool(args.get("heavy", False)),
                    )
                    payload = {"ok": res.exit_code == 0, **res.model_dump(mode="json")}
                elif name == "run_gate_g1":
                    g1_report = await deps.gate_runner.run_g1()
                    g1_passed = g1_report.passed
                    last_gate_summary = g1_report.summary()
                    payload = {
                        "ok": g1_report.passed,
                        "summary": last_gate_summary,
                        "feedback": (
                            "All G1 checks passed."
                            if g1_report.passed
                            else g1_report.to_feedback_prompt()
                        ),
                    }
                elif name == "install_package":
                    eco = str(args.get("ecosystem", ""))
                    if eco not in {"python", "npm"}:
                        raise ValueError(f"Unsupported ecosystem: {eco!r}")
                    pkg_res = await deps.sandbox_mcp.install_package(
                        ecosystem="python" if eco == "python" else "npm",
                        package=str(args.get("package", "")),
                    )
                    if pkg_res.exit_code == 0:
                        latest_task = await deps.repo.get_task(deps.task.id)
                        await deps.repo.update_task_pending_deps(
                            deps.task.id,
                            deps.sandbox_mcp.pending_deps,
                            latest_task.version,
                        )
                    payload = {"ok": pkg_res.exit_code == 0, **pkg_res.model_dump(mode="json")}
                elif name == "propose_contract_change":
                    if deps.contract_protocol is None:
                        raise ValueError("ContractChangeProtocol is not wired on this worker")
                    ctr_name = str(args.get("contract_name", "api"))
                    ctr = await deps.repo.get_contract(state["run_id"], ctr_name)
                    sub_res = await deps.contract_protocol.submit_request(
                        contract_id=ctr.id,
                        requested_by_task_id=deps.task.id,
                        proposed_body=dict(args.get("proposed_body") or {}),
                        reason=str(args.get("reason", "")),
                    )
                    payload = {
                        "ok": sub_res.classification != "pinned_violation",
                        "classification": sub_res.classification,
                        "status": sub_res.status,
                    }
                elif name == "finish_build":
                    done_requested = True
                    payload = {
                        "ok": True,
                        "status": "proceeding_to_g2",
                        "summary": str(args.get("summary", "")),
                    }
                else:
                    payload = {"ok": False, "error": f"Unknown tool: {name}"}
            except PackageApprovalRequiredError as exc:
                payload = {"ok": False, "approval_required": True, "error": str(exc)}
            except (
                WorkspaceViolationError,
                CommandNotAllowedError,
                GitError,
                FileNotFoundError,
                ValueError,
            ) as exc:
                payload = {"ok": False, "error": str(exc)}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            )

        return {
            "messages": messages,
            "last_tool_calls": [],
            "done_requested": done_requested,
            "g1_passed": g1_passed,
            "last_gate_summary": last_gate_summary,
        }

    async def boundary(state: ImplementerState, config: RunnableConfig) -> dict[str, Any]:
        _ = config
        messages = list(state.get("messages", []))

        # 1. Apply buffered contract diffs at loop boundary
        buffered: list[dict[str, Any]] = list(state.get("buffered_contract_diffs", []))
        if deps.contract_protocol is not None:
            buffered.extend(deps.contract_protocol.pop_buffered_diffs(deps.task.id))
        if buffered:
            diff_summary = json.dumps(buffered, sort_keys=True)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Contract update buffered during your active step. "
                        f"Adapt your code before finishing:\n{diff_summary}"
                    ),
                }
            )

        # 2. Check upstream kind='schema' merge requiring boundary rebase + DB reclone (A7-9)
        schema_sha: str | None = None
        if deps.boundary_schema_ref is not None:
            schema_sha = deps.boundary_schema_ref.get("main_sha")
        if not schema_sha:
            schema_sha = state.get("pending_schema_sha")

        if schema_sha:
            changed = await asyncio.to_thread(
                sync_workspace_to_worktree,
                deps.workspace_dir,
                deps.worktree_dir,
                deps.write_set,
                allow_infra=(deps.task.domain == "infra"),
            )
            if changed:
                await deps.git.commit(
                    deps.worktree_dir,
                    f"wip({deps.task.id}): checkpoint before schema boundary rebase",
                    deps.write_set,
                    allow_infra=(deps.task.domain == "infra"),
                )
            rebased, conflicts = await deps.git.rebase_onto(deps.worktree_dir, schema_sha)
            if not rebased:
                await deps.git.abort_rebase(deps.worktree_dir)
                if deps.boundary_schema_ref is not None:
                    deps.boundary_schema_ref["main_sha"] = None
                return {
                    "messages": messages,
                    "buffered_contract_diffs": [],
                    "pending_schema_sha": schema_sha,
                    "needs_clean_restart": True,
                    "final_status": "schema_rebase_restart",
                    "error": f"Boundary rebase conflict after schema merge: {conflicts}",
                }

            sync_worktree_to_workspace(deps.worktree_dir, deps.workspace_dir)
            if deps.reclone_hook is not None:
                await deps.reclone_hook.reclone_after_schema(
                    task_id=deps.task.id,
                    main_sha=schema_sha,
                )
            if deps.boundary_schema_ref is not None:
                deps.boundary_schema_ref["main_sha"] = None
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Schema barrier merged at {schema_sha}; workspace rebased and "
                        "sandbox DB re-cloned cleanly from app_template."
                    ),
                }
            )

        return {
            "messages": messages,
            "buffered_contract_diffs": [],
            "pending_schema_sha": None,
        }

    async def self_check_and_commit(
        state: ImplementerState, config: RunnableConfig
    ) -> dict[str, Any]:
        _ = config
        report = await deps.gate_runner.run_g2()
        deps.last_g2_report = report
        summary = report.summary()

        if report.passed:
            changed = await deps.git_mcp.status()
            sha: str | None = state.get("committed_sha")
            if changed:
                sha = await deps.git_mcp.commit(f"feat({state['task_slug']}): {deps.task.title}")
            return {
                "g2_passed": True,
                "last_gate_summary": summary,
                "committed_sha": sha,
                "final_status": "verified_g2",
                "error": None,
            }

        can_retry = state.get("attempt", 1) < state.get("max_attempts", 3) and state.get(
            "step_index", 0
        ) < state.get("max_steps", 25)
        if can_retry:
            feedback = (
                f"Gate G2 failed on attempt {state.get('attempt', 1)}. "
                "Fix all reported issues and call finish_build again:\n\n"
                f"{report.to_feedback_prompt()}"
            )
            return {
                "g2_passed": False,
                "last_gate_summary": summary,
                "attempt": state.get("attempt", 1) + 1,
                "done_requested": False,
                "messages": [*state.get("messages", []), {"role": "user", "content": feedback}],
                "final_status": "running",
            }

        return {
            "g2_passed": False,
            "last_gate_summary": summary,
            "final_status": "failed",
            "error": report.to_feedback_prompt(),
        }

    def _route_after_act(state: ImplementerState) -> str:
        if state.get("final_status") != "running":
            return str(END)
        if state.get("last_tool_calls"):
            return "tool_node"
        return "boundary"

    def _route_after_boundary(state: ImplementerState) -> str:
        if state.get("needs_clean_restart") or state.get("final_status") == "schema_rebase_restart":
            return str(END)
        if state.get("done_requested") or state.get("step_index", 0) >= state.get("max_steps", 25):
            return "self_check_and_commit"
        return "act"

    def _route_after_commit(state: ImplementerState) -> str:
        if state.get("final_status") == "running":
            return "act"
        return str(END)

    graph = StateGraph(ImplementerState)
    graph.add_node("load_context", load_context)
    graph.add_node("plan", plan)
    graph.add_node("act", act)
    graph.add_node("tool_node", tool_node)
    graph.add_node("boundary", boundary)
    graph.add_node("self_check_and_commit", self_check_and_commit)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "plan")
    graph.add_edge("plan", "act")
    graph.add_conditional_edges(
        "act",
        _route_after_act,
        {"tool_node": "tool_node", "boundary": "boundary", END: END},
    )
    graph.add_edge("tool_node", "boundary")
    graph.add_conditional_edges(
        "boundary",
        _route_after_boundary,
        {
            "act": "act",
            "self_check_and_commit": "self_check_and_commit",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "self_check_and_commit",
        _route_after_commit,
        {"act": "act", END: END},
    )
    return graph.compile(checkpointer=checkpointer)
