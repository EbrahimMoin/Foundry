"""Prompt builders for parallel Module 3 Implementer threads (exact Postgres JSON + diffs)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from foundry.blackboard.models import NoteRecord, TaskRecord

IMPLEMENTER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file inside the sandbox /workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a file strictly inside your task's reserved write set.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files inside a workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string"}},
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run an allowlisted command inside the offline container.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "array", "items": {"type": "string"}},
                    "heavy": {"type": "boolean"},
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_gate_g1",
            "description": "Run Gate G1 (lint, types, tests, OpenAPI check) scoped to write set.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_package",
            "description": "Install a curated or approved dependency into container volumes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ecosystem": {"type": "string", "enum": ["python", "npm"]},
                    "package": {"type": "string"},
                },
                "required": ["ecosystem", "package"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_contract_change",
            "description": "Propose an additive or breaking contract revision via Blackboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract_name": {"type": "string"},
                    "proposed_body": {"type": "object"},
                    "reason": {"type": "string"},
                },
                "required": ["contract_name", "proposed_body", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_build",
            "description": "Signal completion so Gate G2 and daemon commit can execute.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]

IMPLEMENTER_SYSTEM_PROMPT = (
    "You are a Foundry Parallel Implementer agent operating inside an isolated .git-free "
    "Docker sandbox. Follow the exact Postgres contract JSON strictly. Edit ONLY paths inside "
    "your assigned static write_set; never create symlinks or run git. Run `run_gate_g1` "
    "before calling `finish_build`."
)


def build_implementer_messages(
    *,
    task: TaskRecord,
    write_set: Sequence[str],
    contracts: dict[str, dict[str, Any]],
    notes: Sequence[NoteRecord] = (),
) -> list[dict[str, Any]]:
    """Build exact-fact initial messages (zero raw chat logs) preserving prefix-cache order."""
    contract_json = json.dumps(contracts, sort_keys=True, indent=2)
    notes_block = (
        "\n".join(f"- [{n.kind}] {n.body}" for n in notes) if notes else "No prior feature notes."
    )
    acceptance_block = "\n".join(f"- {a}" for a in task.acceptance) or "- Pass G1 and G2 checks."
    user_prompt = (
        f"## Task `{task.id}` (Wave {task.wave}, domain={task.domain}, "
        f"feature={task.feature or 'core'}, attempt={task.attempt})\n"
        f"**Title:** {task.title}\n"
        f"**Description:** {task.description}\n\n"
        f"**Reserved Write Set (strict):**\n"
        + "\n".join(f"- `{p}`" for p in write_set)
        + f"\n\n**Acceptance Criteria:**\n{acceptance_block}\n\n"
        f"**Relevant Feature Notes:**\n{notes_block}\n\n"
        f"## Locked Contracts (Exact JSON)\n```json\n{contract_json}\n```\n"
    )
    return [
        {"role": "system", "content": IMPLEMENTER_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def compact_old_tool_outputs(
    messages: Sequence[dict[str, Any]],
    *,
    keep_recent_messages: int = 6,
    max_tool_chars: int = 280,
) -> list[dict[str, Any]]:
    """Compact tool outputs older than `keep_recent_messages` back to keep prefix caches small."""
    total = len(messages)
    cutoff = max(0, total - keep_recent_messages)
    compacted: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        if idx < cutoff and msg.get("role") == "tool":
            raw = str(msg.get("content", ""))
            if len(raw) > max_tool_chars:
                short = raw[:max_tool_chars].rstrip() + "... [compacted]"
                compacted.append({**msg, "content": short})
                continue
        compacted.append(dict(msg))
    return compacted
