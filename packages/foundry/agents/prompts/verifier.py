"""Blind G3 Black-Box Test-Writer prompt builders and narrow tool schemas."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

from foundry.blackboard.models import TaskRecord

VERIFIER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_contract_file",
            "description": (
                "Read a locked contract ('openapi', 'component', 'db_schema') or 'conftest'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Contract name or 'conftest'.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_test_file",
            "description": "Write a black-box test file strictly within the allowed test paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repository-relative test file path.",
                    },
                    "content": {"type": "string", "description": "Full test source content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_g3_tests",
            "description": "Execute the black-box G3 tests inside the isolated sandbox.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_tests",
            "description": (
                "Finish G3 verification once all black-box tests are written and pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Concise summary of test coverage.",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


def build_verifier_messages(
    *,
    task: TaskRecord,
    contracts: dict[str, dict[str, Any]],
    allowed_test_paths: Sequence[str],
    conftest_source: str,
    domain_kind: Literal["backend", "frontend", "integration"] = "backend",
) -> list[dict[str, Any]]:
    """Build blind black-box system + user prompts for the cross-family G3 Test-Writer."""
    system_prompt = (
        "You are Foundry's blind Cross-Family G3 Black-Box Test-Writer. "
        "STRICT ISOLATION RULES:\n"
        "1. Do NOT inspect internal implementation modules, ORM models, or helpers.\n"
        "2. Write ONLY black-box tests in the allowed test paths.\n"
        "3. For API tests, use `client`, `auth_headers`, and `seed_user(role=...)` from "
        "`conftest.py`, calling ONLY operations in the locked OpenAPI contract. For intentional "
        "invalid-payload negative tests returning 400/422, pass `expect_invalid=True` so "
        "post-test Test-Validator recognizes intentional negative coverage.\n"
        "4. For Frontend component tests (Vitest + Testing Library), use static contract-validated "
        "fixtures, import components ONLY from contract-declared export paths, and query elements "
        "ONLY via contract-declared `data-testid` attributes.\n"
        "5. Always call `run_g3_tests` after writing tests, fix any broken test code or invalid "
        "requests, and call `finish_tests` when complete."
    )

    user_payload = {
        "task": {
            "id": task.id,
            "domain": task.domain,
            "feature": task.feature,
            "kind": domain_kind,
            "title": task.title,
            "description": task.description,
            "acceptance": task.acceptance,
        },
        "allowed_test_paths": list(allowed_test_paths),
        "contracts": contracts,
        "conftest_readonly_excerpt": conftest_source[:3000],
    }

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, indent=2)},
    ]
