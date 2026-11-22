"""Deterministic Stage-4 staged contract validator (Spectral-style conventions + cross-checks)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from foundry.contracts.models import (
    ComponentContract,
    ContractValidationResult,
    DbSchemaContract,
)
from foundry.verification.contract_checks import check_pinned_pointers, diff_openapi_breaking
from foundry.verification.models import ContractViolation

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def validate_openapi_conventions(openapi_doc: dict[str, Any]) -> list[ContractViolation]:
    """Enforce Section 12.2 & A7-7 OpenAPI rules (/api prefix, security, Error, x-foundry-rules)."""
    violations: list[ContractViolation] = []

    components = openapi_doc.get("components", {})
    schemas = components.get("schemas", {}) if isinstance(components, dict) else {}
    if not isinstance(schemas, dict) or "Error" not in schemas:
        violations.append(
            ContractViolation(
                kind="schema_error",
                pointer="/components/schemas/Error",
                message="OpenAPI contract must define shared components.schemas.Error",
            )
        )

    paths = openapi_doc.get("paths", {})
    if not isinstance(paths, dict) or not paths:
        violations.append(
            ContractViolation(
                kind="schema_error",
                pointer="/paths",
                message="OpenAPI contract must declare at least one path under /api/",
            )
        )
        return violations

    for raw_path, item in paths.items():
        path_str = str(raw_path)
        if not path_str.startswith("/api/"):
            violations.append(
                ContractViolation(
                    kind="schema_error",
                    pointer=f"/paths/{path_str}",
                    message=f"API path '{path_str}' must live under /api/",
                )
            )
        if not isinstance(item, dict):
            continue
        for method_key, op_obj in item.items():
            method = str(method_key).lower()
            if method not in _HTTP_METHODS or not isinstance(op_obj, dict):
                continue
            if "security" not in op_obj:
                violations.append(
                    ContractViolation(
                        kind="schema_error",
                        pointer=f"/paths/{path_str}/{method}/security",
                        message=(
                            f"Operation {method.upper()} {path_str} must declare "
                            "'security' (or 'security: []')"
                        ),
                    )
                )
            rules = op_obj.get("x-foundry-rules")
            if not isinstance(rules, str) or not rules.strip():
                violations.append(
                    ContractViolation(
                        kind="schema_error",
                        pointer=f"/paths/{path_str}/{method}/x-foundry-rules",
                        message=(
                            f"Operation {method.upper()} {path_str} must define "
                            "non-empty 'x-foundry-rules' behavioral contract rules"
                        ),
                    )
                )

    return violations


def validate_staged_contracts(
    *,
    db_schema: DbSchemaContract,
    openapi_doc: dict[str, Any],
    components: ComponentContract,
    pinned_openapi: dict[str, Any] | None = None,
    pinned_testids: Sequence[str] = (),
) -> ContractValidationResult:
    """Validate Data -> Backend -> Frontend contracts jointly before locking v1."""
    violations: list[ContractViolation] = []

    violations.extend(validate_openapi_conventions(openapi_doc))

    if pinned_openapi is not None:
        pinned_viols = check_pinned_pointers(pinned_openapi, openapi_doc)
        violations.extend(pinned_viols)
        covered_ptrs = {v.pointer for v in pinned_viols}
        for diff_v in diff_openapi_breaking(pinned_openapi, openapi_doc):
            if "added newly required" in diff_v.message or not any(
                diff_v.pointer == p or diff_v.pointer.startswith(f"{p}/") for p in covered_ptrs
            ):
                violations.append(diff_v)

    # Ensure DB schema defines domain tables when non-health routes exist
    domain_paths = [
        str(p)
        for p in openapi_doc.get("paths", {})
        if str(p) not in {"/api/health"} and not str(p).startswith("/api/auth")
    ]
    if domain_paths and not db_schema.tables:
        violations.append(
            ContractViolation(
                kind="schema_error",
                pointer="/db_schema/tables",
                message="DB schema must define domain tables backing API resources",
            )
        )

    # Check data-testid uniqueness and pinned test-ID coverage
    seen_testids: dict[str, str] = {}
    for route in components.routes:
        if route.test_id:
            if route.test_id in seen_testids:
                violations.append(
                    ContractViolation(
                        kind="schema_error",
                        pointer=f"components/routes/{route.path}",
                        message=(
                            f"Duplicate data-testid '{route.test_id}' "
                            f"(also used in {seen_testids[route.test_id]})"
                        ),
                    )
                )
            else:
                seen_testids[route.test_id] = f"route:{route.path}"

    for comp_name, comp in components.components.items():
        for tid in comp.testids:
            if tid in seen_testids:
                violations.append(
                    ContractViolation(
                        kind="schema_error",
                        pointer=f"components/{comp_name}",
                        message=(
                            f"Duplicate data-testid '{tid}' (also used in {seen_testids[tid]})"
                        ),
                    )
                )
            else:
                seen_testids[tid] = f"component:{comp_name}"

    for req_tid in pinned_testids:
        if req_tid not in seen_testids:
            violations.append(
                ContractViolation(
                    kind="testid_missing",
                    pointer=f"components#{req_tid}",
                    message=f"Component contract missing required pinned data-testid '{req_tid}'",
                )
            )

    return ContractValidationResult(valid=not violations, violations=violations)
