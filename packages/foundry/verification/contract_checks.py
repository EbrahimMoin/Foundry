"""Host-side contract, pinned-pointer, test-ID, and secret checks (zero untrusted exec)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from foundry.security.scanner import SecretScanner
from foundry.security.workspace_guard import normalize_repo_path, validate_workspace_tree
from foundry.verification.models import CheckOutcome, ContractViolation

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_SKIP_SCAN_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".foundry_tmp",
        ".hypothesis",
        "dist",
    }
)
_FRONTEND_EXTS = frozenset({".tsx", ".ts", ".jsx", ".js", ".html"})


class OpenAPIExportError(ValueError):
    """Raised when sandbox-exported OpenAPI JSON is missing or invalid."""


def _escape_json_pointer(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def resolve_schema_ref(
    doc: dict[str, Any],
    schema: dict[str, Any] | None,
    _seen: set[str] | None = None,
) -> dict[str, Any]:
    """Resolve local JSON `$ref` and shallow `allOf` schemas safely."""
    if not isinstance(schema, dict):
        return {}

    seen = set() if _seen is None else set(_seen)
    current = dict(schema)

    ref = current.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/") and ref not in seen:
        seen.add(ref)
        target: Any = doc
        for raw_part in ref[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(target, dict) and part in target:
                target = target[part]
            else:
                target = {}
                break
        if isinstance(target, dict):
            current = resolve_schema_ref(doc, target, seen)

    if isinstance(current.get("allOf"), list):
        merged_props: dict[str, Any] = dict(current.get("properties", {}))
        merged_req: list[str] = list(current.get("required", []))
        for sub in current["allOf"]:
            if isinstance(sub, dict):
                resolved_sub = resolve_schema_ref(doc, sub, seen)
                merged_props.update(resolved_sub.get("properties", {}))
                for req in resolved_sub.get("required", []):
                    if req not in merged_req:
                        merged_req.append(req)
        current["properties"] = merged_props
        if merged_req:
            current["required"] = merged_req

    return current


def _extract_json_schema(operation: dict[str, Any], section: str) -> dict[str, Any] | None:
    if section == "requestBody":
        body = operation.get("requestBody")
        if not isinstance(body, dict):
            return None
        content = body.get("content", {})
        app_json = content.get("application/json", {}) if isinstance(content, dict) else {}
        schema = app_json.get("schema") if isinstance(app_json, dict) else None
        return schema if isinstance(schema, dict) else None
    return None


def _extract_response_schema(operation: dict[str, Any], status: str) -> dict[str, Any] | None:
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return None
    resp_obj = responses.get(status)
    if not isinstance(resp_obj, dict):
        return None
    content = resp_obj.get("content", {})
    app_json = content.get("application/json", {}) if isinstance(content, dict) else {}
    schema = app_json.get("schema") if isinstance(app_json, dict) else None
    return schema if isinstance(schema, dict) else None


def load_exported_openapi(
    workspace_dir: Path,
    rel_path: str = ".foundry_tmp/openapi.json",
) -> dict[str, Any]:
    """Safely load sandbox-exported OpenAPI JSON after running the symlink/FIFO pre-walk."""
    validate_workspace_tree(workspace_dir, ignore_git_dir=False)
    norm_rel = normalize_repo_path(rel_path)
    target = workspace_dir / norm_rel
    if not target.is_file():
        raise OpenAPIExportError(f"Exported OpenAPI artifact not found at {norm_rel}")
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenAPIExportError(f"Invalid exported OpenAPI JSON ({norm_rel}): {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("paths"), dict):
        raise OpenAPIExportError(f"Exported OpenAPI JSON missing 'paths' mapping: {norm_rel}")
    return parsed


def extract_pinned_pointers(pinned_openapi: dict[str, Any]) -> list[str]:
    """Return RFC 6901 pointers for every pinned path, method, response, and required field."""
    pointers: list[str] = []
    paths = pinned_openapi.get("paths", {})
    if not isinstance(paths, dict):
        return pointers

    for path_key, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        esc_path = _escape_json_pointer(str(path_key))
        path_ptr = f"/paths/{esc_path}"
        pointers.append(path_ptr)

        for method_key, op_obj in path_item.items():
            method = str(method_key).lower()
            if method not in _HTTP_METHODS or not isinstance(op_obj, dict):
                continue
            op_ptr = f"{path_ptr}/{method}"
            pointers.append(op_ptr)

            req_schema = resolve_schema_ref(
                pinned_openapi, _extract_json_schema(op_obj, "requestBody")
            )
            for req_field in req_schema.get("required", []):
                pointers.append(f"{op_ptr}/requestBody/required/{_escape_json_pointer(req_field)}")

            responses = op_obj.get("responses", {})
            if isinstance(responses, dict):
                for status_key in responses:
                    status_str = str(status_key)
                    resp_ptr = f"{op_ptr}/responses/{_escape_json_pointer(status_str)}"
                    pointers.append(resp_ptr)
                    resp_schema = resolve_schema_ref(
                        pinned_openapi, _extract_response_schema(op_obj, status_str)
                    )
                    for req_field in resp_schema.get("required", []):
                        pointers.append(f"{resp_ptr}/required/{_escape_json_pointer(req_field)}")
    return pointers


def check_pinned_pointers(
    pinned_openapi: dict[str, Any],
    exported_openapi: dict[str, Any],
) -> list[ContractViolation]:
    """Verify every pinned path, method, status, and required property exists in exported spec."""
    violations: list[ContractViolation] = []
    pinned_paths = pinned_openapi.get("paths", {})
    exported_paths = exported_openapi.get("paths", {})
    if not isinstance(pinned_paths, dict):
        return violations
    if not isinstance(exported_paths, dict):
        return [
            ContractViolation(
                kind="pinned_violation",
                pointer="/paths",
                message="Exported OpenAPI document has no 'paths' object",
            )
        ]

    for path_key, pinned_item in pinned_paths.items():
        if not isinstance(pinned_item, dict):
            continue
        esc_path = _escape_json_pointer(str(path_key))
        path_ptr = f"/paths/{esc_path}"
        exported_item = exported_paths.get(path_key)
        if not isinstance(exported_item, dict):
            violations.append(
                ContractViolation(
                    kind="pinned_violation",
                    pointer=path_ptr,
                    message=f"Missing pinned path '{path_key}' in exported OpenAPI",
                )
            )
            continue

        for method_key, pinned_op in pinned_item.items():
            method = str(method_key).lower()
            if method not in _HTTP_METHODS or not isinstance(pinned_op, dict):
                continue
            op_ptr = f"{path_ptr}/{method}"
            exported_op = exported_item.get(method)
            if not isinstance(exported_op, dict):
                violations.append(
                    ContractViolation(
                        kind="pinned_violation",
                        pointer=op_ptr,
                        message=f"Missing pinned operation {method.upper()} {path_key}",
                    )
                )
                continue

            # Request body required fields
            pinned_req = resolve_schema_ref(
                pinned_openapi, _extract_json_schema(pinned_op, "requestBody")
            )
            pinned_req_fields = list(pinned_req.get("required", []))
            if pinned_req_fields:
                exp_req = resolve_schema_ref(
                    exported_openapi, _extract_json_schema(exported_op, "requestBody")
                )
                exp_props = set(exp_req.get("properties", {}).keys())
                if not exp_props:
                    violations.append(
                        ContractViolation(
                            kind="pinned_violation",
                            pointer=f"{op_ptr}/requestBody",
                            message=(
                                f"Operation {method.upper()} {path_key} missing JSON requestBody"
                            ),
                        )
                    )
                else:
                    for field in pinned_req_fields:
                        if field not in exp_props:
                            violations.append(
                                ContractViolation(
                                    kind="pinned_violation",
                                    pointer=f"{op_ptr}/requestBody/{field}",
                                    message=(
                                        f"Request schema for {method.upper()} {path_key} "
                                        f"missing pinned property '{field}'"
                                    ),
                                )
                            )

            # Responses & 2xx required fields
            pinned_resps = pinned_op.get("responses", {})
            exp_resps = exported_op.get("responses", {})
            if isinstance(pinned_resps, dict):
                for status_key in pinned_resps:
                    status_str = str(status_key)
                    resp_ptr = f"{op_ptr}/responses/{status_str}"
                    if not isinstance(exp_resps, dict) or status_str not in exp_resps:
                        violations.append(
                            ContractViolation(
                                kind="pinned_violation",
                                pointer=resp_ptr,
                                message=(
                                    f"Operation {method.upper()} {path_key} "
                                    f"missing pinned status {status_str}"
                                ),
                            )
                        )
                        continue

                    if status_str.startswith("2"):
                        pinned_resp_schema = resolve_schema_ref(
                            pinned_openapi, _extract_response_schema(pinned_op, status_str)
                        )
                        req_resp_fields = list(pinned_resp_schema.get("required", []))
                        if req_resp_fields:
                            exp_resp_schema = resolve_schema_ref(
                                exported_openapi,
                                _extract_response_schema(exported_op, status_str),
                            )
                            exp_resp_props = set(exp_resp_schema.get("properties", {}).keys())
                            for field in req_resp_fields:
                                if field not in exp_resp_props:
                                    violations.append(
                                        ContractViolation(
                                            kind="pinned_violation",
                                            pointer=f"{resp_ptr}/properties/{field}",
                                            message=(
                                                f"Response {status_str} on {method.upper()} "
                                                f"{path_key} missing pinned field '{field}'"
                                            ),
                                        )
                                    )

    return violations


def diff_openapi_breaking(
    baseline_openapi: dict[str, Any],
    exported_openapi: dict[str, Any],
    *,
    baseline_path: Path | None = None,
    exported_path: Path | None = None,
) -> list[ContractViolation]:
    """Detect breaking OpenAPI drift via structural diff and optional oasdiff CLI."""
    violations: list[ContractViolation] = []
    base_paths = baseline_openapi.get("paths", {})
    exp_paths = exported_openapi.get("paths", {})
    if not isinstance(base_paths, dict) or not isinstance(exp_paths, dict):
        return violations

    for path_key, base_item in base_paths.items():
        if not isinstance(base_item, dict):
            continue
        esc_path = _escape_json_pointer(str(path_key))
        exp_item = exp_paths.get(path_key)
        if not isinstance(exp_item, dict):
            violations.append(
                ContractViolation(
                    kind="breaking_change",
                    pointer=f"/paths/{esc_path}",
                    message=f"Removed baseline path '{path_key}'",
                )
            )
            continue

        for method_key, base_op in base_item.items():
            method = str(method_key).lower()
            if method not in _HTTP_METHODS or not isinstance(base_op, dict):
                continue
            exp_op = exp_item.get(method)
            if not isinstance(exp_op, dict):
                violations.append(
                    ContractViolation(
                        kind="breaking_change",
                        pointer=f"/paths/{esc_path}/{method}",
                        message=f"Removed baseline operation {method.upper()} {path_key}",
                    )
                )
                continue

            # Newly required request body fields not declared in baseline
            base_req = resolve_schema_ref(
                baseline_openapi, _extract_json_schema(base_op, "requestBody")
            )
            exp_req = resolve_schema_ref(
                exported_openapi, _extract_json_schema(exp_op, "requestBody")
            )
            base_req_set = set(base_req.get("required", []))
            exp_req_set = set(exp_req.get("required", []))
            if base_req or base_req_set:
                added_required = sorted(exp_req_set - base_req_set)
                for field in added_required:
                    violations.append(
                        ContractViolation(
                            kind="breaking_change",
                            pointer=f"/paths/{esc_path}/{method}/requestBody/{field}",
                            message=(
                                f"Operation {method.upper()} {path_key} added newly required "
                                f"request field '{field}'"
                            ),
                        )
                    )

            # Removed response statuses or response properties
            base_resps = base_op.get("responses", {})
            exp_resps = exp_op.get("responses", {})
            if isinstance(base_resps, dict) and isinstance(exp_resps, dict):
                for status_key in base_resps:
                    status_str = str(status_key)
                    if status_str not in exp_resps:
                        violations.append(
                            ContractViolation(
                                kind="breaking_change",
                                pointer=f"/paths/{esc_path}/{method}/responses/{status_str}",
                                message=(
                                    f"Removed response status {status_str} on "
                                    f"{method.upper()} {path_key}"
                                ),
                            )
                        )
                        continue
                    if status_str.startswith("2"):
                        base_r_schema = resolve_schema_ref(
                            baseline_openapi, _extract_response_schema(base_op, status_str)
                        )
                        exp_r_schema = resolve_schema_ref(
                            exported_openapi, _extract_response_schema(exp_op, status_str)
                        )
                        base_props = set(base_r_schema.get("properties", {}).keys())
                        exp_props = set(exp_r_schema.get("properties", {}).keys())
                        for removed in sorted(base_props - exp_props):
                            violations.append(
                                ContractViolation(
                                    kind="breaking_change",
                                    pointer=(
                                        f"/paths/{esc_path}/{method}/responses/"
                                        f"{status_str}/properties/{removed}"
                                    ),
                                    message=(
                                        f"Removed response field '{removed}' on "
                                        f"{method.upper()} {path_key} ({status_str})"
                                    ),
                                )
                            )

    oasdiff_bin = shutil.which("oasdiff")
    if (
        oasdiff_bin
        and baseline_path
        and exported_path
        and baseline_path.is_file()
        and exported_path.is_file()
    ):
        res = subprocess.run(  # noqa: S603
            [
                oasdiff_bin,
                "breaking",
                str(baseline_path),
                str(exported_path),
                "--format",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0 and res.stdout.strip():
            try:
                raw_items = json.loads(res.stdout)
            except json.JSONDecodeError:
                raw_items = []
            seen_msgs = {v.message for v in violations}
            if isinstance(raw_items, list):
                for item in raw_items:
                    if isinstance(item, dict):
                        msg = str(item.get("text") or item.get("id") or "oasdiff breaking")
                        if msg not in seen_msgs:
                            violations.append(
                                ContractViolation(
                                    kind="breaking_change",
                                    pointer=str(item.get("path") or "/"),
                                    message=f"oasdiff: {msg}",
                                )
                            )

    return violations


def check_pinned_testids(
    workspace_dir: Path,
    required_testids: Sequence[str],
) -> list[ContractViolation]:
    """Verify every required pinned `data-testid` exists under `frontend/src` safely."""
    validate_workspace_tree(workspace_dir, ignore_git_dir=False)
    if not required_testids:
        return []

    src_dir = workspace_dir / "frontend" / "src"
    if not src_dir.is_dir():
        return [
            ContractViolation(
                kind="testid_missing",
                pointer="frontend/src",
                message="Frontend source directory 'frontend/src' does not exist",
            )
        ]

    combined_parts: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(src_dir, topdown=True, followlinks=False):
        current = Path(dirpath)
        for fname in filenames:
            fpath = current / fname
            if fpath.suffix.lower() in _FRONTEND_EXTS:
                combined_parts.append(fpath.read_text(encoding="utf-8", errors="replace"))
    combined = "\n".join(combined_parts)

    violations: list[ContractViolation] = []
    for tid in required_testids:
        escaped = re.escape(tid)
        pattern = re.compile(
            rf"""(?:data-testid\s*=\s*(?:["']{escaped}["']|\{{\s*["']{escaped}["']\s*\}})|testId\s*:\s*["']{escaped}["'])"""
        )
        if not pattern.search(combined):
            violations.append(
                ContractViolation(
                    kind="testid_missing",
                    pointer=f"frontend/src#{tid}",
                    message=f"Missing required data-testid '{tid}' in frontend/src",
                )
            )
    return violations


def scan_workspace_secrets(
    workspace_dir: Path,
    scanner: SecretScanner | None = None,
) -> list[ContractViolation]:
    """Scan workspace text files for leaked credentials after symlink/FIFO pre-walk."""
    validate_workspace_tree(workspace_dir, ignore_git_dir=False)
    active_scanner = scanner or SecretScanner()
    violations: list[ContractViolation] = []

    for dirpath, dirnames, filenames in os.walk(workspace_dir, topdown=True, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_SCAN_DIRS]
        for fname in filenames:
            fpath = current / fname
            try:
                raw = fpath.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw or len(raw) > 2_097_152:
                continue
            text = raw.decode("utf-8", errors="ignore")
            matches = active_scanner.scan_text(text)
            if matches:
                rel = normalize_repo_path(fpath.relative_to(workspace_dir).as_posix())
                rules = ", ".join(sorted({m.rule for m in matches}))
                violations.append(
                    ContractViolation(
                        kind="secret_leak",
                        pointer=rel,
                        message=f"Potential secret detected in '{rel}' ({rules})",
                    )
                )
    return violations


def run_optional_host_scanners(
    worktree_dir: Path,
    *,
    require_installed: bool = False,
) -> list[CheckOutcome]:
    """Run daemon-side Semgrep and gitleaks only after deterministic symlink/FIFO walk."""
    validate_workspace_tree(worktree_dir, ignore_git_dir=True)
    outcomes: list[CheckOutcome] = []

    scanner_cmds: tuple[tuple[str, list[str]], ...] = (
        (
            "semgrep",
            ["semgrep", "--max-memory", "512", "--jobs", "1", "--error", "--quiet", "."],
        ),
        (
            "gitleaks",
            ["gitleaks", "detect", "--no-git", "--source", "."],
        ),
    )

    for name, cmd in scanner_cmds:
        binary = shutil.which(cmd[0])
        if binary is None:
            outcomes.append(
                CheckOutcome(
                    name=name,
                    passed=not require_installed,
                    blocking=require_installed,
                    exit_code=0 if not require_installed else 127,
                    detail="skipped (binary not installed on host)",
                )
            )
            continue

        resolved_cmd = [binary, *cmd[1:]]
        proc = subprocess.run(  # noqa: S603
            resolved_cmd,
            cwd=str(worktree_dir),
            check=False,
            capture_output=True,
            text=True,
        )
        outcomes.append(
            CheckOutcome(
                name=name,
                passed=proc.returncode == 0,
                blocking=True,
                exit_code=proc.returncode,
                detail="passed" if proc.returncode == 0 else f"{name} found issues",
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        )

    return outcomes
