"""System prompt, tool schemas, and prompt builder for SingleAgentBuilder (v0.1 / B1)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

SINGLE_AGENT_SYSTEM_PROMPT = """\
You are Foundry Single-Agent Builder, an autonomous full-stack software engineer working inside a
hardened Wave 0 `fastapi-react-postgres` monorepo sandbox.

Architecture & Conventions:
1. `backend/app/main.py` auto-discovers every `backend/app/features/<feature>/router.py` module
   that defines `router = APIRouter(...)` and mounts it under `/api`.
2. Backend code lives in `backend/app/features/<feature>/` (or `backend/tests/` for unit tests).
   All Python code must pass `ruff check`, strict `mypy`, and `pytest`.
3. Frontend code lives in `frontend/src/features/<feature>/`. Every feature must define all
   required `data-testid` attributes and export nav metadata in `feature.ts`.
4. Never touch `.git*` files, never create symlinks, and stay strictly inside your reserved
   `write_set`.
5. Keep all pinned OpenAPI paths, HTTP methods, status codes, and required request/response fields
   exact (`pinned_openapi.yaml`).

Workflow:
- Inspect the scaffold if needed (`list_files`, `read_file`).
- Write modular, clean backend and frontend files via `write_file`.
- Run `run_gate_g1` to verify ruff, mypy, pytest, OpenAPI export, and contract conformance.
- Once G1 passes and all requirements are implemented, call `finish_build` to enter Gate G2.
"""

SINGLE_AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 file inside the sandbox workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repository-relative file path (e.g. backend/app/main.py).",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file strictly within the reserved write set.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repository-relative file path inside write_set.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete UTF-8 file content.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List repository-relative files under a workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Relative directory path (defaults to '.').",
                        "default": ".",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run an allowlisted offline tool command inside the sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Allowlisted binary and arguments.",
                    },
                    "heavy": {
                        "type": "boolean",
                        "description": "Set true for heavy test/typecheck commands.",
                        "default": False,
                    },
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_gate_g1",
            "description": (
                "Run Gate G1 self-check (ruff, mypy, pytest, OpenAPI export, pinned checks)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "List changed repository paths detected daemon-side.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "View daemon-side unified git diff of workspace edits.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_package",
            "description": "Install an allowlisted or human-approved python/npm package.",
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
            "name": "finish_build",
            "description": "Signal completion and proceed to blocking Gate G2 verification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Concise summary of implemented features and checks.",
                    }
                },
                "required": ["summary"],
            },
        },
    },
]


def build_initial_user_prompt(
    spec: str,
    *,
    app_name: str,
    write_set: Sequence[str],
    pinned_openapi_yaml: str | None = None,
    pinned_testids_yaml: str | None = None,
) -> str:
    """Format the initial user prompt containing spec, write set, and pinned contracts."""
    parts = [
        f"# Application: {app_name}",
        f"Allowed write_set prefixes: {', '.join(write_set)}",
        "",
        "## Product Specification",
        spec.strip(),
    ]
    if pinned_openapi_yaml:
        parts.extend(
            ["", "## Pinned OpenAPI 3.1 Contract Stub (DO NOT BREAK)", pinned_openapi_yaml]
        )
    if pinned_testids_yaml:
        parts.extend(["", "## Pinned Frontend data-testid Selectors", pinned_testids_yaml])
    return "\n".join(parts)
