"""Pydantic v2 request and response schemas for the Foundry Daemon API (Section 21.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from foundry.blackboard.models import (
    ContractVersionRecord,
    LlmCallRecord,
    TaskRecord,
    WriteReservationRecord,
)
from foundry.config.schemas import AutonomyOverrides, AutonomyPreset


class ProjectSummary(BaseModel):
    """Project descriptor aggregated from `.foundry/project.yaml` and Blackboard runs."""

    id: str
    name: str
    stack_pack: str
    run_count: int = 0
    latest_run_id: str | None = None
    latest_phase: str | None = None


class RunControlResponse(BaseModel):
    """Outcome of a run lifecycle control action (pause/resume/stop)."""

    run_id: str
    phase: str
    ended_at: datetime | None = None


class TaskDetailResponse(BaseModel):
    """Detailed view of a Blackboard task with write sets, dependencies, and active locks."""

    task: TaskRecord
    write_set: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    reservations: list[WriteReservationRecord] = Field(default_factory=list)


class ContractVersionsResponse(BaseModel):
    """Contract record paired with its immutable version history."""

    contract_id: str
    name: str
    domain: str
    kind: str
    status: str
    current_version: int
    versions: list[ContractVersionRecord] = Field(default_factory=list)


class DecisionResolveRequest(BaseModel):
    """Payload to resolve a pending Blackboard decision via Dashboard or API."""

    action: Literal["approve", "reject"] = "approve"
    choice: Any | None = None
    answer: Any | None = None
    feedback: str | None = None
    decided_by: str = "human"


class ModelCostBucket(BaseModel):
    """Aggregated spend and token telemetry for a single model or profile."""

    key: str
    calls: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost_usd: float
    est_value_usd: float


class CostsSummaryResponse(BaseModel):
    """Cost and prefix-cache telemetry summary for a run (`GET /api/runs/{id}/costs`)."""

    run_id: str
    budget_usd: float
    spent_usd: float
    total_call_cost_usd: float
    total_est_value_usd: float
    call_count: int
    cache_hit_rate: float
    by_model: list[ModelCostBucket] = Field(default_factory=list)
    by_profile: list[ModelCostBucket] = Field(default_factory=list)
    calls: list[LlmCallRecord] = Field(default_factory=list)


class FeatureMetricsSummary(BaseModel):
    """Per-feature verification and dispute telemetry."""

    name: str
    status: str
    budget_blackbox_tests_usd: float
    spent_blackbox_tests_usd: float
    dispute_rounds: int


class MetricsSummaryResponse(BaseModel):
    """Operational metrics summary for a run (`GET /api/runs/{id}/metrics`)."""

    run_id: str
    phase: str
    tasks_total: int
    tasks_by_status: dict[str, int] = Field(default_factory=dict)
    total_attempts: int = 0
    total_contract_reopens: int = 0
    active_reservations: int = 0
    contracts_count: int = 0
    pending_decisions: int = 0
    dispute_rounds_total: int = 0
    features: list[FeatureMetricsSummary] = Field(default_factory=list)
    events_count: int = 0


class ModelAssignmentsUpdate(BaseModel):
    """Update role -> model profile assignments in `models.yaml`."""

    roles: dict[str, str]


class AutonomyUpdate(BaseModel):
    """Update project autonomy preset, category overrides, or confidence threshold."""

    preset: AutonomyPreset | None = None
    overrides: AutonomyOverrides | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class DataPolicyUpdate(BaseModel):
    """Update project privacy/data policy switches."""

    allow_free_tier: bool
    allow_trace_export: bool = False


class PreviewUrlResponse(BaseModel):
    """Resolved preview URL for the Dashboard iframe (`GET /api/preview/{run_id}/url`)."""

    run_id: str
    env: Literal["local", "staging", "production"]
    url: str | None = None
    available: bool = False
