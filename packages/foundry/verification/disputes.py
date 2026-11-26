"""Section 9.7 Test Dispute Protocol (`x-foundry-rules`, Lead ruling, and 2-round cap)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel

from foundry.verification.models import DisputeResolution, OutboundRequestTrace

MAX_DISPUTE_ROUNDS_PER_FEATURE = 2

_STATUS_ASSERT_RE = re.compile(
    r"(?:status_code[^\n]*?(\d{3})[^\n]*(?:==|!=)[^\n]*(\d{3})|\bassert\s+(\d{3})\s*==\s*(\d{3}))"
)
_SINGLE_STATUS_ASSERT_RE = re.compile(r"status_code\s*==\s*(\d{3})")
_KEY_ASSERT_RE = re.compile(
    r"(?:KeyError:\s*['\"]([A-Za-z0-9_]+)['\"]|assert\s+['\"]([A-Za-z0-9_]+)['\"]\s+in\s+)"
)


class LeadRulingResponse(BaseModel):
    """Structured ruling returned by an owning Domain Lead during Step 2 dispute resolution."""

    verdict: Literal["test_wrong", "code_wrong", "contract_clarified"]
    reason: str
    clarified_rules: dict[str, Any] | None = None


LeadRulingFn = Callable[
    [str, dict[str, Any], OutboundRequestTrace | None, str],
    Awaitable[LeadRulingResponse],
]


def _match_openapi_operation(
    openapi_doc: dict[str, Any],
    method: str,
    path: str,
) -> dict[str, Any] | None:
    paths = openapi_doc.get("paths") or {}
    if not isinstance(paths, dict):
        return None
    m = method.lower()
    direct = paths.get(path)
    if isinstance(direct, dict) and isinstance(direct.get(m), dict):
        return dict(direct[m])

    for tmpl, methods in paths.items():
        if not isinstance(methods, dict) or m not in methods:
            continue
        seg_parts = [
            "[^/]+" if seg.startswith("{") and seg.endswith("}") else re.escape(seg)
            for seg in str(tmpl).strip("/").split("/")
        ]
        regex = "^/$" if str(tmpl) == "/" else "^/" + "/".join(seg_parts) + "$"
        if re.match(regex, path) and isinstance(methods[m], dict):
            return dict(methods[m])
    return None


def _resolve_ref(spec: dict[str, Any], schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        cur: Any = spec
        for part in ref[2:].split("/"):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return {}
        if isinstance(cur, dict):
            return cur
    return schema


def _declared_response_properties(
    spec: dict[str, Any],
    op: dict[str, Any],
    status_code: int | None,
) -> set[str]:
    responses = op.get("responses") or {}
    if not isinstance(responses, dict) or status_code is None:
        return set()
    resp = responses.get(str(status_code)) or responses.get(status_code)
    if not isinstance(resp, dict):
        return set()
    schema = ((resp.get("content") or {}).get("application/json") or {}).get("schema")
    resolved = _resolve_ref(spec, schema)
    props = resolved.get("properties") or {}
    return {str(k) for k in props} if isinstance(props, dict) else set()


def _extract_expected_status(failure_output: str, actual_status: int | None) -> int | None:
    match = _STATUS_ASSERT_RE.search(failure_output)
    if match:
        nums = [int(g) for g in match.groups() if g is not None]
        if len(nums) == 2:
            left, right = nums
            if actual_status is not None:
                if left == actual_status and right != actual_status:
                    return right
                if right == actual_status and left != actual_status:
                    return left
            return right
    single = _SINGLE_STATUS_ASSERT_RE.search(failure_output)
    if single:
        return int(single.group(1))
    return None


def evaluate_contract_dispute(
    openapi_doc: dict[str, Any],
    trace: OutboundRequestTrace | None,
    failure_output: str,
) -> tuple[Literal["test_wrong", "code_wrong", "ambiguous"], str]:
    """Step 1 deterministic contract + `x-foundry-rules` check before invoking a Domain Lead."""
    if trace is None:
        if "500" in failure_output:
            return "code_wrong", "Server returned 500 internal error during black-box test."
        return "ambiguous", "No outbound request trace available for deterministic contract check."

    op = _match_openapi_operation(openapi_doc, trace.method, trace.path)
    if op is None:
        return (
            "test_wrong",
            f"Operation {trace.method} {trace.path} is missing from locked OpenAPI contract.",
        )

    responses = op.get("responses") or {}
    declared_codes = {str(k) for k in responses} if isinstance(responses, dict) else set()
    actual = trace.status_code
    expected = _extract_expected_status(failure_output, actual)

    # 1. Test asserted a status code or response field not in the locked contract -> test_wrong
    if expected is not None and declared_codes and str(expected) not in declared_codes:
        codes_str = sorted(declared_codes)
        return (
            "test_wrong",
            f"Test asserted status {expected}, undeclared in contract responses {codes_str}.",
        )

    key_match = _KEY_ASSERT_RE.search(failure_output)
    if key_match is not None and actual is not None:
        asserted_key = key_match.group(1) or key_match.group(2)
        props = _declared_response_properties(openapi_doc, op, actual)
        if asserted_key and props and asserted_key not in props:
            return (
                "test_wrong",
                f"Test asserted field '{asserted_key}' not declared in {actual} response schema.",
            )

    # 2. Server returned an undeclared status code (e.g., 500) on a valid request -> code_wrong
    if actual is not None and (
        actual >= 500 or (declared_codes and str(actual) not in declared_codes)
    ):
        return (
            "code_wrong",
            f"Implementation returned undeclared HTTP {actual} for {trace.method} {trace.path}.",
        )

    # 3. Both statuses declared: check if explicit x-foundry-rules resolve it or route to Step 2
    rules = op.get("x-foundry-rules")
    if isinstance(rules, dict) and expected is not None and actual is not None:
        status_rule = rules.get(str(expected)) or rules.get(f"status_{expected}")
        actual_rule = rules.get(str(actual)) or rules.get(f"status_{actual}")
        if (
            (status_rule and actual != expected) or (actual_rule and actual != expected)
        ) and "ambiguous" not in failure_output.lower():
            return (
                "code_wrong",
                f"x-foundry-rules precondition violated (expected {expected}, got {actual}).",
            )

    # 4. Response schema mismatch indicators from pydantic/json assertions
    if "ValidationError" in failure_output or "missing required field" in failure_output.lower():
        return (
            "code_wrong",
            f"Response payload for {trace.method} {trace.path} violated locked response schema.",
        )

    return (
        "ambiguous",
        (
            f"Status mismatch (expected={expected}, actual={actual}) on "
            f"{trace.method} {trace.path} exists in contract and requires Lead ruling."
        ),
    )


class TestDisputeProtocol:
    """Runs Section 9.7 Step 1 -> Step 2 Lead ruling -> Step 3 Orchestrator escalation."""

    __test__ = False

    def __init__(self, *, max_rounds_per_feature: int = MAX_DISPUTE_ROUNDS_PER_FEATURE) -> None:
        self._max_rounds = max_rounds_per_feature

    async def resolve_dispute(
        self,
        *,
        feature: str,
        openapi_doc: dict[str, Any],
        trace: OutboundRequestTrace | None,
        failure_output: str,
        current_dispute_rounds: int,
        lead_ruling_fn: LeadRulingFn | None = None,
    ) -> DisputeResolution:
        next_round = current_dispute_rounds + 1
        if next_round > self._max_rounds:
            return DisputeResolution(
                verdict="escalated_orchestrator",
                step=3,
                reason=(
                    f"Feature '{feature}' exceeded max {self._max_rounds} G3 dispute rounds; "
                    "escalating to Orchestrator."
                ),
                dispute_round=next_round,
            )

        step1_verdict, step1_reason = evaluate_contract_dispute(openapi_doc, trace, failure_output)
        if step1_verdict in {"test_wrong", "code_wrong"}:
            return DisputeResolution(
                verdict=step1_verdict,
                step=1,
                reason=step1_reason,
                dispute_round=next_round,
            )

        if lead_ruling_fn is not None:
            ruling = await lead_ruling_fn(feature, openapi_doc, trace, failure_output)
            return DisputeResolution(
                verdict=ruling.verdict,
                step=2,
                reason=ruling.reason,
                clarified_rules=ruling.clarified_rules,
                dispute_round=next_round,
            )

        return DisputeResolution(
            verdict="escalated_orchestrator",
            step=3,
            reason=f"{step1_reason} (no Domain Lead ruling callback configured)",
            dispute_round=next_round,
        )
