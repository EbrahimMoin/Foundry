"""Blind cross-family G3 Test-Writer LangGraph worker (`{run}:verify:{feature}:{round}`)."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from foundry.agents.prompts.verifier import VERIFIER_TOOLS, build_verifier_messages
from foundry.blackboard.models import TaskRecord
from foundry.blackboard.repository import BlackboardRepository
from foundry.gateway.protocol import GatewayRequest
from foundry.gateway.router import BudgetExceededError, GatewayPolicyError, ModelGateway
from foundry.sandbox.protocol import ExecPriority, Sandbox, SandboxProvider
from foundry.security.hardened_git import HardenedGit
from foundry.security.workspace_guard import (
    WorkspaceViolationError,
    is_forbidden_git_meta,
    normalize_repo_path,
    path_in_write_set,
    validate_workspace_tree,
)
from foundry.verification.disputes import (
    LeadRulingFn,
    LeadRulingResponse,
    TestDisputeProtocol,
)
from foundry.verification.models import DisputeResolution, TestValidationResult
from foundry.verification.test_validator import classify_g3_failure, load_request_traces

_TRACE_REL_PATH = ".foundry_tmp/g3_request_trace.jsonl"
_LOCKED_OPENAPI_REL_PATH = ".foundry_tmp/locked_openapi.json"


def verifier_thread_id(run_id: str, feature: str, round_num: int) -> str:
    """Canonical independent thread identifier (`{run}:verify:{feature}:{round}`, Sec 7.2)."""
    return f"{run_id}:verify:{feature}:{round_num}"


class G3VerificationResult(BaseModel):
    """Structured outcome returned by executing Gate G3 verification."""

    task_id: str
    feature: str
    status: Literal[
        "verified",
        "code_wrong",
        "escalated_orchestrator",
        "infra_failed",
        "budget_exceeded",
    ]
    committed_test_sha: str | None = None
    test_files: list[str] = Field(default_factory=list)
    self_fix_attempts: int = 0
    dispute_resolution: DisputeResolution | None = None
    validation: TestValidationResult | None = None
    summary: str = ""
    cost_usd: float = 0.0


@dataclass
class VerifierGraphDeps:
    """Runtime dependencies bound into an isolated G3 verification thread."""

    gateway: ModelGateway
    provider: SandboxProvider
    sandbox: Sandbox
    repo: BlackboardRepository
    task: TaskRecord
    allowed_test_paths: list[str]
    contracts: dict[str, dict[str, Any]]
    worktree_dir: Path | None = None
    git: HardenedGit | None = None
    task_write_set: list[str] = field(default_factory=list)
    peer_implementer_family: str | None = None
    lead_ruling_fn: LeadRulingFn | None = None
    dispute_protocol: TestDisputeProtocol = field(default_factory=TestDisputeProtocol)
    declared_testids: list[str] = field(default_factory=list)
    domain_kind: Literal["backend", "frontend", "integration"] = "backend"
    max_self_fix_attempts: int = 2
    max_steps: int = 12


class VerifierState(TypedDict, total=False):
    run_id: str
    task_id: str
    feature: str
    round_num: int
    step_index: int
    messages: list[dict[str, Any]]
    last_tool_calls: list[dict[str, Any]]
    written_test_files: list[str]
    self_fix_attempts: int
    infra_retries: int
    tests_run_once: bool
    last_validation: dict[str, Any] | None
    last_dispute: dict[str, Any] | None
    cost_usd: float
    committed_test_sha: str | None
    final_status: str
    summary: str


def _safe_write_test_file(
    workspace_dir: Path,
    rel_path: str,
    content: str,
    allowed_test_paths: Sequence[str],
) -> str:
    norm = normalize_repo_path(rel_path)
    if is_forbidden_git_meta(norm, allow_infra=False):
        raise WorkspaceViolationError(f"Forbidden git path in test writer: {norm}")
    if not path_in_write_set(norm, allowed_test_paths):
        raise WorkspaceViolationError(
            f"Test path '{norm}' is outside G3 allowed test paths {list(allowed_test_paths)}"
        )
    dest = workspace_dir / norm
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return norm


def _read_test_sources(workspace_dir: Path, test_files: Sequence[str]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for rel in test_files:
        p = workspace_dir / rel
        if p.is_file():
            sources[rel] = p.read_text(encoding="utf-8", errors="replace")
    return sources


def _commit_passing_g3_tests(
    *,
    sandbox_workspace: Path,
    worktree_dir: Path,
    written_files: Sequence[str],
    allowed_test_paths: Sequence[str],
) -> list[str]:
    """Validate workspace tree + copy ONLY allowed G3 test files into task worktree (`A8-1`)."""
    validate_workspace_tree(sandbox_workspace, ignore_git_dir=False)
    copied: list[str] = []
    for raw in written_files:
        norm = normalize_repo_path(raw)
        if not path_in_write_set(norm, allowed_test_paths):
            raise WorkspaceViolationError(
                f"Refusing to commit non-test G3 file outside allowlist: {norm}"
            )
        src = sandbox_workspace / norm
        if src.is_file():
            dst = worktree_dir / norm
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(norm)
    return copied


def build_verifier_graph(
    deps: VerifierGraphDeps,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Compile the blind G3 Test-Writer graph with post-test `Test-Validator` + disputes."""
    feature_name = deps.task.feature or deps.task.domain or "feature"

    async def _default_lead_ruling(
        feature: str,
        openapi_doc: dict[str, Any],
        trace: Any,
        failure_output: str,
    ) -> LeadRulingResponse:
        req = GatewayRequest(
            role="domain_lead",
            domain=deps.task.domain,
            feature=feature,
            task_id=deps.task.id,
            task_slug=f"dispute_{feature}",
            node="g3_dispute_ruling",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the owning Domain Lead ruling on a G3 black-box dispute. "
                        'Respond strictly with JSON: {"verdict": '
                        '"test_wrong|code_wrong|contract_clarified", '
                        '"reason": str, "clarified_rules": dict|null}.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "feature": feature,
                            "trace": trace.model_dump(mode="json") if trace else None,
                            "failure_output": failure_output[-2000:],
                            "openapi_paths": list((openapi_doc.get("paths") or {}).keys()),
                        }
                    ),
                },
            ],
        )
        resp = await deps.gateway.complete(req)
        payload = json.loads(resp.content)
        return LeadRulingResponse.model_validate(payload)

    async def load_context(state: VerifierState, config: RunnableConfig) -> dict[str, Any]:
        _ = config
        openapi_doc = deps.contracts.get("api") or deps.contracts.get("openapi")
        if isinstance(openapi_doc, dict):
            locked_file = deps.sandbox.workspace_dir / _LOCKED_OPENAPI_REL_PATH
            locked_file.parent.mkdir(parents=True, exist_ok=True)
            locked_file.write_text(json.dumps(openapi_doc, indent=2), encoding="utf-8")

        if state.get("messages"):
            return {}
        conftest_path = deps.sandbox.workspace_dir / "backend/tests/conftest.py"
        conftest_src = (
            conftest_path.read_text(encoding="utf-8", errors="replace")
            if conftest_path.is_file()
            else ""
        )
        msgs = build_verifier_messages(
            task=deps.task,
            contracts=deps.contracts,
            allowed_test_paths=deps.allowed_test_paths,
            conftest_source=conftest_src,
            domain_kind=deps.domain_kind,
        )
        return {
            "messages": msgs,
            "written_test_files": list(state.get("written_test_files", [])),
            "self_fix_attempts": int(state.get("self_fix_attempts", 0)),
            "infra_retries": int(state.get("infra_retries", 0)),
            "cost_usd": float(state.get("cost_usd", 0.0)),
            "final_status": "running",
        }

    async def write_tests(state: VerifierState, config: RunnableConfig) -> dict[str, Any]:
        _ = config
        step = int(state.get("step_index", 0))
        if step >= deps.max_steps:
            return {
                "final_status": "escalated_orchestrator",
                "summary": f"Exceeded G3 max steps ({deps.max_steps}).",
                "last_tool_calls": [],
            }

        req = GatewayRequest(
            role="test_writer",
            domain=deps.task.domain,
            feature=feature_name,
            task_id=deps.task.id,
            task_slug=f"g3_{feature_name}",
            node="g3_test_writer",
            step_index=step,
            attempt=int(state.get("round_num", 1)),
            peer_implementer_family=deps.peer_implementer_family,
            messages=list(state.get("messages", [])),
            tools=VERIFIER_TOOLS,
        )
        try:
            resp = await deps.gateway.complete(req)
        except BudgetExceededError as exc:
            return {
                "final_status": "budget_exceeded",
                "summary": str(exc),
                "last_tool_calls": [],
            }
        except GatewayPolicyError as exc:
            return {
                "final_status": "escalated_orchestrator",
                "summary": str(exc),
                "last_tool_calls": [],
            }

        await deps.repo.upsert_feature(
            deps.task.run_id,
            feature_name,
            status="in_progress",
            spent_delta_usd=resp.cost_usd,
        )
        await deps.repo.record_llm_call(
            run_id=deps.task.run_id,
            task_id=deps.task.id,
            graph_thread_id=verifier_thread_id(
                deps.task.run_id, feature_name, int(state.get("round_num", 1))
            ),
            node="g3_test_writer",
            provider=resp.provider_id,
            model=resp.model,
            profile="test_writer",
            transport=str(resp.transport),
            billing_mode=str(resp.billing_mode),
            input_tokens=resp.tokens_in,
            cached_tokens=resp.tokens_cached,
            output_tokens=resp.tokens_out,
            cost_usd=resp.cost_usd,
            est_value_usd=resp.est_value_usd,
            replayed=resp.replayed,
            prompt_hash=resp.prompt_hash,
        )

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
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": resp.content}
        if formatted_calls:
            assistant_msg["tool_calls"] = formatted_calls

        return {
            "messages": [*state.get("messages", []), assistant_msg],
            "step_index": step + 1,
            "cost_usd": float(state.get("cost_usd", 0.0)) + resp.cost_usd,
            "last_tool_calls": [
                {"id": tc.id, "name": tc.name, "args": tc.args} for tc in resp.tool_calls
            ],
        }

    async def execute_tools_and_validate(
        state: VerifierState, config: RunnableConfig
    ) -> dict[str, Any]:
        _ = config
        if state.get("final_status") not in {None, "", "running"}:
            return {}

        messages = list(state.get("messages", []))
        written = list(state.get("written_test_files", []))
        self_fix_attempts = int(state.get("self_fix_attempts", 0))
        infra_retries = int(state.get("infra_retries", 0))
        tests_run_once = bool(state.get("tests_run_once", False))
        last_validation = state.get("last_validation")
        last_dispute = state.get("last_dispute")
        final_status = "running"
        summary = str(state.get("summary", ""))

        calls = list(state.get("last_tool_calls", []))
        if not calls:
            # Treat bare text completion as requesting test run
            calls = [{"id": "auto_run_g3", "name": "run_g3_tests", "args": {}}]

        for call in calls:
            call_id = str(call["id"])
            name = str(call["name"])
            args = call.get("args") or {}
            payload: dict[str, Any]

            if name == "read_contract_file":
                key = str(args.get("name", "api"))
                if key == "conftest":
                    cpath = deps.sandbox.workspace_dir / "backend/tests/conftest.py"
                    payload = {
                        "ok": cpath.is_file(),
                        "content": cpath.read_text(encoding="utf-8") if cpath.is_file() else "",
                    }
                else:
                    payload = {"ok": key in deps.contracts, "contract": deps.contracts.get(key)}
            elif name == "write_test_file":
                try:
                    written_path = _safe_write_test_file(
                        deps.sandbox.workspace_dir,
                        str(args.get("path", "")),
                        str(args.get("content", "")),
                        deps.allowed_test_paths,
                    )
                    if written_path not in written:
                        written.append(written_path)
                    payload = {"ok": True, "path": written_path}
                except WorkspaceViolationError as exc:
                    payload = {"ok": False, "error": str(exc)}
            elif name == "finish_tests":
                summary = str(args.get("summary", summary))
                if not tests_run_once or (
                    last_validation and last_validation.get("outcome") != "passed"
                ):
                    payload = {
                        "ok": False,
                        "error": "Cannot finish before run_g3_tests passes cleanly.",
                    }
                else:
                    final_status = "verified"
                    payload = {"ok": True, "summary": summary}
            elif name == "run_g3_tests":
                tests_run_once = True
                trace_abs = deps.sandbox.workspace_dir / _TRACE_REL_PATH
                if trace_abs.exists():
                    trace_abs.unlink()

                if deps.domain_kind == "frontend":
                    targets = written or [p for p in deps.allowed_test_paths]
                    cmd = ["pnpm", "--dir", "frontend", "test", "--run", *targets]
                else:
                    targets = written or [p for p in deps.allowed_test_paths]
                    cmd = [
                        "pytest",
                        "-q",
                        "-o",
                        "cache_dir=/tmp/.pytest_cache",
                        "-o",
                        "asyncio_mode=auto",
                        *targets,
                    ]

                exec_res = await deps.provider.exec(
                    deps.sandbox,
                    cmd,
                    network=False,
                    heavy=True,
                    priority=ExecPriority.VERIFY_G3,
                )
                traces = await asyncio.to_thread(load_request_traces, deps.sandbox.workspace_dir)
                sources = await asyncio.to_thread(
                    _read_test_sources, deps.sandbox.workspace_dir, written
                )
                validation = classify_g3_failure(
                    exec_res.exit_code,
                    exec_res.stdout,
                    exec_res.stderr,
                    traces=traces,
                    test_sources=sources,
                    declared_testids=deps.declared_testids,
                    timed_out=exec_res.timed_out,
                )
                last_validation = validation.model_dump(mode="json")

                if validation.outcome == "passed":
                    final_status = "verified"
                    summary = summary or "G3 black-box test suite passed."
                    payload = {"ok": True, "validation": last_validation}
                elif validation.outcome == "infra_failure":
                    infra_retries += 1
                    if infra_retries > 1:
                        final_status = "infra_failed"
                        summary = validation.reason
                    payload = {"ok": False, "validation": last_validation}
                elif validation.outcome in {"broken_test_code", "invalid_test_request"}:
                    self_fix_attempts += 1
                    if self_fix_attempts > deps.max_self_fix_attempts:
                        final_status = "escalated_orchestrator"
                        summary = (
                            f"Test-Writer exceeded {deps.max_self_fix_attempts} self-fix attempts: "
                            f"{validation.reason}"
                        )
                    payload = {"ok": False, "validation": last_validation}
                else:
                    # Real assertion failure -> Section 9.7 Test Dispute Protocol
                    feat_rec = await deps.repo.get_feature(deps.task.run_id, feature_name)
                    current_rounds = feat_rec.dispute_rounds if feat_rec is not None else 0
                    openapi_doc = deps.contracts.get("api") or deps.contracts.get("openapi") or {}
                    ruling_cb = deps.lead_ruling_fn or _default_lead_ruling
                    dispute_res = await deps.dispute_protocol.resolve_dispute(
                        feature=feature_name,
                        openapi_doc=openapi_doc,
                        trace=validation.failing_trace,
                        failure_output=validation.detail,
                        current_dispute_rounds=current_rounds,
                        lead_ruling_fn=ruling_cb,
                    )
                    last_dispute = dispute_res.model_dump(mode="json")
                    await deps.repo.upsert_feature(
                        deps.task.run_id,
                        feature_name,
                        status="in_progress",
                        dispute_rounds_delta=1,
                    )
                    if dispute_res.verdict == "code_wrong":
                        final_status = "code_wrong"
                        summary = dispute_res.reason
                    elif dispute_res.verdict == "escalated_orchestrator":
                        final_status = "escalated_orchestrator"
                        summary = dispute_res.reason
                        thread_id = await deps.repo.ensure_thread(
                            run_id=deps.task.run_id,
                            kind="dispute",
                            subject_id=feature_name,
                        )
                        await deps.repo.post_message(
                            thread_id=thread_id,
                            from_id="test_validator",
                            body=dispute_res.reason,
                        )
                        await deps.repo.emit_event(
                            run_id=deps.task.run_id,
                            event_type="verify.dispute_escalated",
                            subject_id=deps.task.id,
                            payload={
                                "feature": feature_name,
                                "dispute_round": dispute_res.dispute_round,
                                "reason": dispute_res.reason,
                            },
                        )
                    else:
                        # test_wrong or contract_clarified -> Test-Writer updates test
                        self_fix_attempts += 1
                        if dispute_res.clarified_rules:
                            rules_json = json.dumps(dispute_res.clarified_rules)
                            await deps.repo.post_note(
                                run_id=deps.task.run_id,
                                task_id=deps.task.id,
                                feature=feature_name,
                                kind="note",
                                body=f"G3 Lead clarified x-foundry-rules: {rules_json}",
                            )
                        if self_fix_attempts > deps.max_self_fix_attempts:
                            final_status = "escalated_orchestrator"
                            summary = dispute_res.reason
                    payload = {
                        "ok": False,
                        "validation": last_validation,
                        "dispute": last_dispute,
                    }
            else:
                payload = {"ok": False, "error": f"Unknown tool '{name}'"}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(payload),
                }
            )

        return {
            "messages": messages,
            "written_test_files": written,
            "self_fix_attempts": self_fix_attempts,
            "infra_retries": infra_retries,
            "tests_run_once": tests_run_once,
            "last_validation": last_validation,
            "last_dispute": last_dispute,
            "final_status": final_status,
            "summary": summary,
        }

    async def commit_tests_node(state: VerifierState, config: RunnableConfig) -> dict[str, Any]:
        _ = config
        if state.get("final_status") != "verified":
            return {}

        written = list(state.get("written_test_files", []))
        committed_sha: str | None = None
        await asyncio.to_thread(
            validate_workspace_tree, deps.sandbox.workspace_dir, ignore_git_dir=False
        )
        if deps.worktree_dir is not None and deps.git is not None and written:
            copied = await asyncio.to_thread(
                _commit_passing_g3_tests,
                sandbox_workspace=deps.sandbox.workspace_dir,
                worktree_dir=deps.worktree_dir,
                written_files=written,
                allowed_test_paths=deps.allowed_test_paths,
            )
            combined_ws = list(dict.fromkeys([*deps.task_write_set, *deps.allowed_test_paths]))
            await deps.git.enforce_write_set(deps.worktree_dir, combined_ws)
            if copied:
                committed_sha = await deps.git.commit(
                    deps.worktree_dir,
                    f"test(g3): {feature_name} black-box verification ({deps.task.id})",
                    combined_ws,
                )

        await deps.repo.upsert_feature(deps.task.run_id, feature_name, status="verified")
        await deps.repo.emit_event(
            run_id=deps.task.run_id,
            event_type="verify.g3_passed",
            subject_id=deps.task.id,
            payload={
                "feature": feature_name,
                "test_files": written,
                "committed_test_sha": committed_sha,
            },
        )
        return {"committed_test_sha": committed_sha}

    def _route_after_tools(
        state: VerifierState,
    ) -> Literal["write_tests", "commit_tests", "__end__"]:
        status = state.get("final_status", "running")
        if status == "verified":
            return "commit_tests"
        if status == "running":
            return "write_tests"
        return "__end__"

    graph = StateGraph(VerifierState)
    graph.add_node("load_context", load_context)
    graph.add_node("write_tests", write_tests)
    graph.add_node("execute_tools", execute_tools_and_validate)
    graph.add_node("commit_tests", commit_tests_node)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "write_tests")
    graph.add_edge("write_tests", "execute_tools")
    graph.add_conditional_edges("execute_tools", _route_after_tools)
    graph.add_edge("commit_tests", END)
    return graph.compile(checkpointer=checkpointer)


async def run_g3_verification(
    deps: VerifierGraphDeps,
    *,
    round_num: int = 1,
    checkpointer: Any | None = None,
) -> G3VerificationResult:
    """Execute one isolated G3 verification round on thread `{run}:verify:{feature}:{round}`."""
    feature_name = deps.task.feature or deps.task.domain or "feature"
    thread_id = verifier_thread_id(deps.task.run_id, feature_name, round_num)
    compiled = build_verifier_graph(deps, checkpointer=checkpointer)
    init_state: VerifierState = {
        "run_id": deps.task.run_id,
        "task_id": deps.task.id,
        "feature": feature_name,
        "round_num": round_num,
        "step_index": 0,
    }
    final_state: VerifierState = await compiled.ainvoke(
        init_state,
        config={"configurable": {"thread_id": thread_id}},
    )
    val_raw = final_state.get("last_validation")
    disp_raw = final_state.get("last_dispute")
    raw_status = str(final_state.get("final_status", "escalated_orchestrator"))
    status_lit: Literal[
        "verified",
        "code_wrong",
        "escalated_orchestrator",
        "infra_failed",
        "budget_exceeded",
    ] = (
        raw_status  # type: ignore[assignment]
        if raw_status
        in {"verified", "code_wrong", "escalated_orchestrator", "infra_failed", "budget_exceeded"}
        else "escalated_orchestrator"
    )
    return G3VerificationResult(
        task_id=deps.task.id,
        feature=feature_name,
        status=status_lit,
        committed_test_sha=final_state.get("committed_test_sha"),
        test_files=list(final_state.get("written_test_files", [])),
        self_fix_attempts=int(final_state.get("self_fix_attempts", 0)),
        dispute_resolution=(DisputeResolution.model_validate(disp_raw) if disp_raw else None),
        validation=(TestValidationResult.model_validate(val_raw) if val_raw else None),
        summary=str(final_state.get("summary", "")),
        cost_usd=float(final_state.get("cost_usd", 0.0)),
    )
