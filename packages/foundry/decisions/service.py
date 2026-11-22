"""Binary decision service (`Auto` vs `Approve`/`Human`), risk escalation, and ADRs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from foundry.blackboard.models import DecisionRecord
from foundry.blackboard.repository import BlackboardRepository
from foundry.config.schemas import AutonomyConfig, AutonomyPreset, DecisionMode

DecisionCategory = Literal["strategic", "tactical", "operational", "release_infra"]
Reversibility = Literal["reversible", "hard", "irreversible"]

PRESET_MATRIX: dict[AutonomyPreset, dict[DecisionCategory, DecisionMode]] = {
    AutonomyPreset.AUTOPILOT: {
        "strategic": DecisionMode.AUTO,
        "tactical": DecisionMode.AUTO,
        "operational": DecisionMode.AUTO,
        "release_infra": DecisionMode.APPROVE,
    },
    AutonomyPreset.BALANCED: {
        "strategic": DecisionMode.APPROVE,
        "tactical": DecisionMode.APPROVE,
        "operational": DecisionMode.AUTO,
        "release_infra": DecisionMode.APPROVE,
    },
    AutonomyPreset.ARCHITECT: {
        "strategic": DecisionMode.HUMAN,
        "tactical": DecisionMode.APPROVE,
        "operational": DecisionMode.AUTO,
        "release_infra": DecisionMode.APPROVE,
    },
    AutonomyPreset.FULL_CONTROL: {
        "strategic": DecisionMode.HUMAN,
        "tactical": DecisionMode.HUMAN,
        "operational": DecisionMode.APPROVE,
        "release_infra": DecisionMode.APPROVE,
    },
    AutonomyPreset.BENCHMARK: {
        "strategic": DecisionMode.AUTO,
        "tactical": DecisionMode.AUTO,
        "operational": DecisionMode.AUTO,
        "release_infra": DecisionMode.AUTO,
    },
}


class DecisionResolution(BaseModel):
    """Structured resolution payload supplied by CLI Decision Inbox or UI."""

    action: Literal["approve", "reject"]
    choice: Any | None = None
    feedback: str | None = None
    decided_by: str = "human"


def resolve_decision_mode(
    autonomy_cfg: AutonomyConfig,
    *,
    category: DecisionCategory,
    confidence: float | None = 1.0,
    reversibility: Reversibility = "reversible",
    deploy_target: str = "local_compose",
    is_unplanned_package: bool = False,
) -> DecisionMode:
    """Resolve binary decision mode enforcing Section 17.3 safety & package rules."""
    if autonomy_cfg.preset == AutonomyPreset.BENCHMARK and deploy_target != "local_compose":
        raise ValueError("Benchmark autonomy preset is only allowed with local_compose target")

    override = getattr(autonomy_cfg.overrides, category, None)
    mode = override if override is not None else PRESET_MATRIX[autonomy_cfg.preset][category]

    # A7-10: Unplanned non-curated packages always require hard human Approve
    if is_unplanned_package:
        return DecisionMode.APPROVE

    # Cloud release/infra is hardcoded never below Approve
    if (
        category == "release_infra"
        and deploy_target != "local_compose"
        and mode == DecisionMode.AUTO
    ):
        return DecisionMode.APPROVE

    # Low-confidence or irreversible decisions escalate from Auto to Approve
    low_conf = confidence is not None and confidence < autonomy_cfg.confidence_threshold
    if (low_conf or reversibility == "irreversible") and mode == DecisionMode.AUTO:
        return DecisionMode.APPROVE

    return mode


def is_auto_resolved(decision: DecisionRecord) -> bool:
    """Return True when a decision was auto-approved without waiting for a human."""
    return decision.status == "auto_decided"


def _write_adr_sync(adr_dir: Path, decision: DecisionRecord) -> Path:
    adr_dir.mkdir(parents=True, exist_ok=True)
    adr_path = adr_dir / f"ADR-{decision.id}.md"
    decided_ts = (decision.decided_at or datetime.now(UTC)).isoformat()
    lines = [
        f"# ADR {decision.id}: {decision.title}",
        "",
        f"- **Run ID:** `{decision.run_id}`",
        f"- **Category:** `{decision.category}`",
        f"- **Mode:** `{decision.mode}`",
        f"- **Status:** `{decision.status}`",
        f"- **Decided By:** `{decision.decided_by or 'unknown'}`",
        f"- **Decided At:** `{decided_ts}`",
        "",
        "## Context",
        decision.context,
        "",
        "## Decision",
        f"Answer: `{decision.answer}`",
    ]
    adr_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return adr_path


class DecisionService:
    """Idempotent decision creation & resolution service backing LangGraph `interrupt()`."""

    def __init__(
        self,
        repo: BlackboardRepository,
        *,
        autonomy_cfg: AutonomyConfig | None = None,
        deploy_target: str = "local_compose",
        adr_dir: Path | None = None,
    ) -> None:
        self._repo = repo
        self._autonomy_cfg = autonomy_cfg or AutonomyConfig()
        self._deploy_target = deploy_target
        self._adr_dir = adr_dir

    async def get_or_create(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        category: DecisionCategory,
        title: str,
        context: str,
        options: list[Any],
        recommendation: Any | None = None,
        confidence: float = 1.0,
        reversibility: Reversibility = "reversible",
        impact: dict[str, Any] | None = None,
        graph_thread_id: str | None = None,
        blocks_task_ids: tuple[str, ...] | list[str] = (),
        is_unplanned_package: bool = False,
    ) -> DecisionRecord:
        mode = resolve_decision_mode(
            self._autonomy_cfg,
            category=category,
            confidence=confidence,
            reversibility=reversibility,
            deploy_target=self._deploy_target,
            is_unplanned_package=is_unplanned_package,
        )
        initial_status = "auto_decided" if mode == DecisionMode.AUTO else "pending"
        initial_answer = (
            {"action": "approve", "choice": recommendation, "feedback": None}
            if mode == DecisionMode.AUTO
            else None
        )
        initial_decider = "auto" if mode == DecisionMode.AUTO else None

        decision, created = await self._repo.get_or_create_decision(
            run_id=run_id,
            idempotency_key=idempotency_key,
            category=category,
            title=title,
            context=context,
            options=options,
            mode=mode.value,
            status=initial_status,
            recommendation=recommendation,
            confidence=confidence,
            reversibility=reversibility,
            impact=impact,
            answer=initial_answer,
            decided_by=initial_decider,
            graph_thread_id=graph_thread_id,
            blocks_task_ids=list(blocks_task_ids),
        )
        if created and decision.status == "auto_decided" and self._adr_dir is not None:
            await asyncio.to_thread(_write_adr_sync, self._adr_dir, decision)
        return decision

    async def resolve(
        self,
        decision_id: str,
        *,
        action: Literal["approve", "reject"],
        choice: Any | None = None,
        feedback: str | None = None,
        decided_by: str = "human",
    ) -> DecisionRecord:
        current = await self._repo.get_decision(decision_id)
        if current.status != "pending":
            raise ValueError(
                f"Decision '{decision_id}' cannot be resolved (status={current.status})"
            )
        answer_payload = {
            "action": action,
            "choice": choice if choice is not None else current.recommendation,
            "feedback": feedback,
        }
        updated = await self._repo.resolve_decision(
            decision_id,
            current.version,
            status="resolved",
            answer=answer_payload,
            decided_by=decided_by,
        )
        if self._adr_dir is not None:
            await asyncio.to_thread(_write_adr_sync, self._adr_dir, updated)
        return updated
