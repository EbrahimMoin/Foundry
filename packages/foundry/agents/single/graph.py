"""LangGraph single-agent implementer loop (`SingleAgentBuilder` / Baseline `B1`)."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from foundry.agents.single.prompts import SINGLE_AGENT_TOOLS
from foundry.gateway.protocol import GatewayRequest
from foundry.gateway.router import BudgetExceededError, ModelGateway
from foundry.mcp_servers.git_server import GitMCPServer
from foundry.mcp_servers.sandbox_server import (
    CommandNotAllowedError,
    PackageApprovalRequiredError,
    SandboxMCPServer,
)
from foundry.security.hardened_git import GitError
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


class SingleAgentState(TypedDict):
    """State carried through the Single-Agent Builder LangGraph loop."""

    spec: str
    app_name: str
    task_slug: str
    messages: list[dict[str, Any]]
    step_index: int
    max_steps: int
    attempt: int
    max_attempts: int
    last_tool_calls: list[dict[str, Any]]
    done_requested: bool
    g1_passed: bool
    g2_passed: bool
    last_gate_summary: str
    committed_sha: str | None
    error: str | None
    final_status: str


@dataclass
class SingleAgentDeps:
    """Injected runtime dependencies bound into the compiled SingleAgentBuilder graph."""

    gateway: ModelGateway
    sandbox_mcp: SandboxMCPServer
    git_mcp: GitMCPServer
    gate_runner: GateRunner
    workspace_dir: Path
    task_id: str
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
            rel_path = (cur / fname).relative_to(workspace_dir).as_posix()
            results.append(rel_path)
    return results


def _format_assistant_message(content: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def build_single_agent_graph(deps: SingleAgentDeps) -> Any:
    """Compile the single-agent LangGraph state machine for implement -> G1 -> G2 -> commit."""

    async def agent_step(state: SingleAgentState) -> dict[str, Any]:
        req = GatewayRequest(
            role="implementer",
            task_id=deps.task_id,
            task_slug=state["task_slug"],
            node="agent_step",
            step_index=state["step_index"],
            attempt=state["attempt"],
            messages=state["messages"],
            tools=SINGLE_AGENT_TOOLS,
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
        next_messages = [
            *state["messages"],
            _format_assistant_message(resp.content, formatted_calls),
        ]
        return {
            "messages": next_messages,
            "step_index": state["step_index"] + 1,
            "last_tool_calls": [
                {"id": tc.id, "name": tc.name, "args": tc.args} for tc in resp.tool_calls
            ],
            "done_requested": len(resp.tool_calls) == 0,
        }

    async def tool_node(state: SingleAgentState) -> dict[str, Any]:
        messages = list(state["messages"])
        done_requested = state["done_requested"]
        g1_passed = state["g1_passed"]
        last_gate_summary = state["last_gate_summary"]

        for call in state["last_tool_calls"]:
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
                elif name == "git_status":
                    changed = await deps.git_mcp.status()
                    payload = {"ok": True, "changed_paths": changed}
                elif name == "git_diff":
                    diff_txt = await deps.git_mcp.diff()
                    payload = {"ok": True, "diff": diff_txt}
                elif name == "install_package":
                    eco = str(args.get("ecosystem", ""))
                    if eco not in {"python", "npm"}:
                        raise ValueError(f"Unsupported ecosystem: {eco!r}")
                    pkg_res = await deps.sandbox_mcp.install_package(
                        ecosystem="python" if eco == "python" else "npm",
                        package=str(args.get("package", "")),
                    )
                    payload = {"ok": pkg_res.exit_code == 0, **pkg_res.model_dump(mode="json")}
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

    async def run_gate_g2(state: SingleAgentState) -> dict[str, Any]:
        report = await deps.gate_runner.run_g2()
        deps.last_g2_report = report
        summary = report.summary()

        if report.passed:
            changed = await deps.git_mcp.status()
            sha: str | None = state["committed_sha"]
            if changed:
                sha = await deps.git_mcp.commit(
                    f"feat({state['task_slug']}): implement {state['app_name']}"
                )
            return {
                "g2_passed": True,
                "last_gate_summary": summary,
                "committed_sha": sha,
                "final_status": "passed",
                "error": None,
            }

        can_retry = (
            state["attempt"] < state["max_attempts"] and state["step_index"] < state["max_steps"]
        )
        if can_retry:
            feedback = (
                f"Gate G2 failed on attempt {state['attempt']}. "
                "Fix all reported issues and call finish_build again:\n\n"
                f"{report.to_feedback_prompt()}"
            )
            return {
                "g2_passed": False,
                "last_gate_summary": summary,
                "attempt": state["attempt"] + 1,
                "done_requested": False,
                "messages": [*state["messages"], {"role": "user", "content": feedback}],
                "final_status": "running",
            }

        return {
            "g2_passed": False,
            "last_gate_summary": summary,
            "final_status": "failed",
            "error": report.to_feedback_prompt(),
        }

    def _route_after_agent(state: SingleAgentState) -> str:
        if state["final_status"] != "running":
            return str(END)
        if state["last_tool_calls"]:
            return "tool_node"
        return "run_gate_g2"

    def _route_after_tools(state: SingleAgentState) -> str:
        if state["done_requested"] or state["step_index"] >= state["max_steps"]:
            return "run_gate_g2"
        return "agent_step"

    def _route_after_g2(state: SingleAgentState) -> str:
        if state["final_status"] == "running":
            return "agent_step"
        return str(END)

    graph = StateGraph(SingleAgentState)
    graph.add_node("agent_step", agent_step)
    graph.add_node("tool_node", tool_node)
    graph.add_node("run_gate_g2", run_gate_g2)

    graph.add_edge(START, "agent_step")
    graph.add_conditional_edges(
        "agent_step",
        _route_after_agent,
        {"tool_node": "tool_node", "run_gate_g2": "run_gate_g2", END: END},
    )
    graph.add_conditional_edges(
        "tool_node",
        _route_after_tools,
        {"agent_step": "agent_step", "run_gate_g2": "run_gate_g2"},
    )
    graph.add_conditional_edges(
        "run_gate_g2",
        _route_after_g2,
        {"agent_step": "agent_step", END: END},
    )
    return graph.compile()
