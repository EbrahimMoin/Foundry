"""Risk-based Gate G4 review trigger and cross-family LLM reviewer (Section 11.1)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

from foundry.gateway.protocol import GatewayRequest
from foundry.gateway.router import ModelGateway
from foundry.verification.models import G4Finding, G4ReviewResult, RiskAssessment

_SENSITIVE_PATH_TOKENS = (
    "/auth",
    "auth/",
    "/security",
    "security/",
    "rbac",
    "jwt",
    "permission",
    "oauth",
    "crypto",
    "vault",
    "secret",
)

_SHARED_PREFIXES = (
    "backend/app/core/",
    "backend/app/models/",
    "frontend/src/components/ui/",
    "frontend/src/app/",
)

_MIGRATION_PREFIXES = ("backend/migrations/",)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _deterministic_sample(task_id: str, sample_rate: float) -> bool:
    if sample_rate <= 0.0:
        return False
    if sample_rate >= 1.0:
        return True
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:8]
    unit = int(digest, 16) / 0xFFFFFFFF
    return unit < sample_rate


def should_run_g4(
    task_id: str,
    changed_files: Sequence[str],
    diff_lines: int,
    *,
    sample_rate: float = 0.25,
) -> RiskAssessment:
    """Evaluate G4 triggers (auth/security, shared, migrations, >300 lines, 25% sample)."""
    reasons: list[str] = []
    norm_files = [f.replace("\\", "/").lstrip("./") for f in changed_files]

    if any(any(tok in f.lower() for tok in _SENSITIVE_PATH_TOKENS) for f in norm_files):
        reasons.append("auth_or_security_path")

    if any(f.startswith(_SHARED_PREFIXES) for f in norm_files):
        reasons.append("shared_code_path")

    if any(f.startswith(_MIGRATION_PREFIXES) for f in norm_files):
        reasons.append("database_migration")

    if diff_lines > 300:
        reasons.append(f"large_diff_{diff_lines}_lines")

    sampled = _deterministic_sample(task_id, sample_rate)
    if sampled and not reasons:
        reasons.append(f"random_sample_{int(sample_rate * 100)}pct")

    return RiskAssessment(
        should_run=bool(reasons),
        reasons=reasons,
        sampled=sampled,
        diff_lines=diff_lines,
    )


def _extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    match = _JSON_BLOCK_RE.search(text)
    if match:
        text = match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Reviewer response did not contain a JSON object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Reviewer JSON payload must be an object")
    return parsed


class G4Reviewer:
    """Invokes a cross-family `@verifier` (`role='reviewer'`) for risk-triggered Gate G4."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def review_task(
        self,
        *,
        task_id: str,
        task_title: str,
        task_description: str,
        changed_files: Sequence[str],
        diff_text: str,
        contracts: dict[str, dict[str, Any]],
        peer_implementer_family: str | None = None,
        g2_summary: str = "",
        g3_summary: str = "",
        sample_rate: float = 0.25,
        force: bool = False,
        agent_instance: str | None = None,
    ) -> G4ReviewResult:
        diff_lines = len([ln for ln in diff_text.splitlines() if ln.strip()])
        risk = should_run_g4(task_id, changed_files, diff_lines, sample_rate=sample_rate)
        if not risk.should_run and not force:
            return G4ReviewResult(
                approved=True,
                skipped=True,
                risk=risk,
                summary="G4 review skipped (low risk, outside deterministic 25% sample).",
            )

        req = GatewayRequest(
            role="reviewer",
            task_id=task_id,
            agent_instance=agent_instance,
            task_slug=f"g4_{task_id}",
            node="g4_review",
            peer_implementer_family=peer_implementer_family,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Foundry Gate G4 senior security & architecture reviewer. "
                        "Inspect logic, edge cases, security boundaries, and contract intent. "
                        'Respond strictly with JSON: {"approved": bool, "summary": str, '
                        '"findings": [{"severity": "low|medium|high|critical", '
                        '"path": str, "message": str}]}.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task_id": task_id,
                            "title": task_title,
                            "description": task_description,
                            "risk_triggers": risk.reasons,
                            "changed_files": list(changed_files),
                            "g2_summary": g2_summary,
                            "g3_summary": g3_summary,
                            "contracts": contracts,
                            "diff_excerpt": diff_text[:8000],
                        },
                        indent=2,
                    ),
                },
            ],
        )
        resp = await self._gateway.complete(req)
        try:
            payload = _extract_json_object(resp.content)
        except Exception as exc:
            return G4ReviewResult(
                approved=False,
                skipped=False,
                risk=risk,
                findings=[
                    G4Finding(
                        severity="high",
                        path="",
                        message=f"Malformed G4 reviewer JSON response: {exc}",
                    )
                ],
                summary="G4 review failed due to malformed reviewer output.",
                cost_usd=resp.cost_usd,
                model_used=f"{resp.provider_id}/{resp.model}",
            )

        raw_findings = payload.get("findings") or []
        findings = [
            G4Finding.model_validate(item) for item in raw_findings if isinstance(item, dict)
        ]
        has_blocking = any(f.severity in {"high", "critical"} for f in findings)
        approved = bool(payload.get("approved", False)) and not has_blocking
        return G4ReviewResult(
            approved=approved,
            skipped=False,
            risk=risk,
            findings=findings,
            summary=str(payload.get("summary", "")),
            cost_usd=resp.cost_usd,
            model_used=f"{resp.provider_id}/{resp.model}",
        )
