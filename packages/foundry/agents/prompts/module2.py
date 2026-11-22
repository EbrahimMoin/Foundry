"""Versioned prompts and JSON parsers for Orchestrator and Domain Leads (Module 2)."""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are Foundry Orchestrator, designing structured requirements, architecture, and reviewing
cross-domain contract consistency for a FastAPI + React (Vite/TS) + Postgres monorepo.
Always respond with a single valid JSON object with no markdown prose outside JSON.
"""

LEAD_SYSTEM_PROMPTS: dict[str, str] = {
    "data": (
        "You are Foundry Data Lead. Design the normalized PostgreSQL schema (`db_schema`) JSON "
        "matching the DbSchemaContract schema: {version: 1, tables: {<name>: {columns: [...], "
        "indexes: [...]}}}. Return strictly a valid JSON object."
    ),
    "backend": (
        "You are Foundry Backend Lead. Expand any pinned OpenAPI 3.1 contract using exact JSON "
        "paths under /api/, explicit security, shared components.schemas.Error, and concrete "
        "`x-foundry-rules` on EVERY HTTP operation. Return strictly a valid OpenAPI JSON object."
    ),
    "frontend": (
        "You are Foundry Frontend Lead. Design ComponentContract JSON ({version: 1, routes: [...], "
        "components: {<name>: {export_path, props, emits, testids}}}) aligned with db_schema and "
        "OpenAPI, keeping pinned data-testids unique. Return strictly a valid JSON object."
    ),
    "infra": (
        "You are Foundry Infra Lead. Produce DepsContract JSON ({python_packages: [...], "
        "npm_packages: [...], ci_workflow: '.github/workflows/ci.yml', deploy_target: "
        "'local_compose'}) using only curated dependencies unless strictly necessary. Return JSON."
    ),
}


def parse_json_object(raw_content: str) -> dict[str, Any]:
    """Parse strict JSON object from LLM output, stripping optional code fences."""
    cleaned = raw_content.strip()
    match = _JSON_FENCE_RE.match(cleaned)
    if match:
        cleaned = match.group(1).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object from LLM output, got {type(parsed).__name__}")
    return parsed


def build_requirements_messages(spec: str, *, feedback: str | None = None) -> list[dict[str, Any]]:
    user_parts = [
        "Analyze the specification below and emit JSON: "
        '{"summary": str, "features": [{"name": str, "description": str}], '
        '"roles": [str], "non_functional": [str]}.',
        "",
        "## Product Specification",
        spec.strip(),
    ]
    if feedback:
        user_parts.extend(["", "## Human Review Feedback to Address", feedback.strip()])
    return [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def build_architecture_messages(
    spec: str,
    requirements: dict[str, Any],
    *,
    feedback: str | None = None,
) -> list[dict[str, Any]]:
    user_parts = [
        "Given the approved requirements below, design the architecture JSON: "
        '{"stack_pack": "fastapi-react-postgres", "auth_scheme": "bearer_jwt", '
        '"features": [{"name": str, "backend_module": str, "frontend_module": str, '
        '"tables": [str]}], "schema_changes": [str]}.',
        "",
        "## Specification",
        spec.strip(),
        "",
        "## Approved Requirements (Exact JSON)",
        json.dumps(requirements, sort_keys=True, indent=2),
    ]
    if feedback:
        user_parts.extend(["", "## Human Review Feedback to Address", feedback.strip()])
    return [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def build_lead_contract_messages(
    *,
    domain: str,
    spec: str,
    requirements: dict[str, Any],
    architecture: dict[str, Any],
    upstream_contracts: dict[str, Any],
    pinned_openapi_yaml: str | None = None,
    pinned_testids_yaml: str | None = None,
) -> list[dict[str, Any]]:
    sys_prompt = LEAD_SYSTEM_PROMPTS[domain]
    parts = [
        f"Domain: {domain}",
        "",
        "## Specification",
        spec.strip(),
        "",
        "## Approved Requirements (Exact JSON)",
        json.dumps(requirements, sort_keys=True, indent=2),
        "",
        "## Approved Architecture (Exact JSON)",
        json.dumps(architecture, sort_keys=True, indent=2),
    ]
    if upstream_contracts:
        parts.extend(
            [
                "",
                "## Upstream Locked/Draft Contracts (Exact JSON)",
                json.dumps(upstream_contracts, sort_keys=True, indent=2),
            ]
        )
    if pinned_openapi_yaml and domain == "backend":
        parts.extend(["", "## Pinned OpenAPI Stub (DO NOT BREAK)", pinned_openapi_yaml.strip()])
    if pinned_testids_yaml and domain == "frontend":
        parts.extend(["", "## Pinned data-testid Selectors", pinned_testids_yaml.strip()])
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": "\n".join(parts)},
    ]


def build_consistency_review_messages(
    *,
    contracts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Review all staged contracts for cross-domain consistency (DB tables, OpenAPI "
                "operations + x-foundry-rules, frontend routes/testids, infra deps). Return JSON: "
                '{"consistent": bool, "issues": [str]}.\n\n'
                + json.dumps(contracts, sort_keys=True, indent=2)
            ),
        },
    ]
