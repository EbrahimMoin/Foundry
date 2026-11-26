"""Deterministic post-test `Test-Validator` for Gate G3 (`A7-7`)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from foundry.security.workspace_guard import normalize_repo_path, validate_workspace_tree
from foundry.verification.models import OutboundRequestTrace, TestValidationResult

_INFRA_FAILURE_RE = re.compile(
    r"(?:ConnectionRefusedError|CannotConnectNowError|PoolTimeout|"
    r"OperationalError[^\n]*(?:could not connect|connection refused|timeout)|"
    r"asyncpg\.exceptions\.(?:CannotConnectNowError|TooManyConnectionsError)|"
    r"TimeoutError|deadlock detected)",
    re.IGNORECASE,
)

_BROKEN_CODE_RE = re.compile(
    r"(?:ERROR collecting|SyntaxError|IndentationError|ImportError|"
    r"ModuleNotFoundError|FixtureLookupError|fixture '[^']+' not found|"
    r"Failed to resolve import)",
    re.IGNORECASE,
)

_TEST_TRACEBACK_FRAME_RE = re.compile(
    r'(?:File "[^"]*(?:test_[^"/\\]+\.py|_test\.py|\.test\.[tj]sx?)")'
    r"|(?:(?:tests?|__tests__)/[^\s:]+:\d+:)",
    re.IGNORECASE,
)

_TEST_NAME_ATTR_ERROR_RE = re.compile(r"\b(?:NameError|AttributeError)\b:")

_TESTID_CALL_RE = re.compile(
    r"""(?:(?:get|find|query)(?:All)?ByTestId\(\s*['"]([^'"]+)['"]\s*\)|data-testid\s*=\s*['"]([^'"]+)['"])"""
)


def load_request_traces(
    workspace_dir: Path,
    rel_path: str = ".foundry_tmp/g3_request_trace.jsonl",
) -> list[OutboundRequestTrace]:
    """Safely load outbound HTTP request-validation traces recorded by G3 `conftest.py`."""
    norm = normalize_repo_path(rel_path)
    trace_file = workspace_dir / norm
    if not trace_file.is_file():
        return []
    validate_workspace_tree(trace_file, ignore_git_dir=False)
    traces: list[OutboundRequestTrace] = []
    for raw_line in trace_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            traces.append(OutboundRequestTrace.model_validate(data))
    return traces


def find_unknown_testids(
    test_sources: dict[str, str],
    declared_testids: Sequence[str],
) -> list[str]:
    """Return any `data-testid` queried or asserted in test files missing from the contract."""
    declared = set(declared_testids)
    if not declared:
        return []
    unknown: list[str] = []
    for source in test_sources.values():
        for match in _TESTID_CALL_RE.finditer(source):
            tid = match.group(1) or match.group(2)
            if tid and tid not in declared and tid not in unknown:
                unknown.append(tid)
    return unknown


def classify_g3_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
    *,
    traces: Sequence[OutboundRequestTrace] = (),
    test_sources: dict[str, str] | None = None,
    declared_testids: Sequence[str] = (),
    timed_out: bool = False,
) -> TestValidationResult:
    """Classify G3 test failures post-test without pre-assertion HTTP short-circuiting (`A7-7`)."""
    if exit_code == 0 and not timed_out:
        return TestValidationResult(
            outcome="passed",
            route_to="none",
            reason="All black-box tests passed.",
        )

    combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)

    # 1. Infrastructure / DB failures (never blamed on Test-Writer or Implementer)
    if timed_out or _INFRA_FAILURE_RE.search(combined):
        return TestValidationResult(
            outcome="infra_failure",
            route_to="infra",
            reason="Test run hit an infrastructure or database timeout/connection error.",
            detail=combined[-1600:],
        )

    # 2. Broken test code (collection, syntax, imports, fixtures, test-frame errors, testids)
    if declared_testids and test_sources:
        unknown_ids = find_unknown_testids(test_sources, declared_testids)
        if unknown_ids:
            joined = ", ".join(unknown_ids)
            return TestValidationResult(
                outcome="broken_test_code",
                route_to="test_writer",
                reason=f"Test references undeclared contract data-testid(s): {joined}",
                unknown_testids=unknown_ids,
                detail=combined[-1600:],
            )

    if _BROKEN_CODE_RE.search(combined):
        return TestValidationResult(
            outcome="broken_test_code",
            route_to="test_writer",
            reason="Test failed collection, import resolution, syntax, or fixture lookup.",
            detail=combined[-1600:],
        )

    if _TEST_NAME_ATTR_ERROR_RE.search(combined) and _TEST_TRACEBACK_FRAME_RE.search(combined):
        return TestValidationResult(
            outcome="broken_test_code",
            route_to="test_writer",
            reason="NameError or AttributeError raised inside test code.",
            detail=combined[-1600:],
        )

    # 3. Post-test invalid_test_request check (failed + 400/422 + invalid request + not expected)
    for trace in traces:
        if not trace.request_valid and not trace.expect_invalid and trace.status_code in {400, 422}:
            return TestValidationResult(
                outcome="invalid_test_request",
                route_to="test_writer",
                reason=(
                    f"Test sent schema-invalid request to {trace.method} {trace.path} "
                    f"(HTTP {trace.status_code}) without expect_invalid=True: "
                    f"{trace.validation_error or 'schema violation'}"
                ),
                failing_trace=trace,
                detail=combined[-1600:],
            )

    # 4. Real behavioral / contract assertion failure -> Dispute protocol (Section 9.7)
    return TestValidationResult(
        outcome="assertion_failure",
        route_to="dispute",
        reason="Test failed with a real assertion mismatch against valid contract requests.",
        failing_trace=traces[-1] if traces else None,
        detail=combined[-1600:],
    )
