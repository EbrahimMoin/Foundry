"""SQLAlchemy 2 ORM models and Pydantic v2 DTOs for the Postgres Blackboard (Section 8.2)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TaskStatus(StrEnum):
    """Task lifecycle states (Section 8.2 / 9.5)."""

    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    VERIFIED = "VERIFIED"
    QUEUED = "QUEUED"
    MERGED = "MERGED"
    DEPLOYED = "DEPLOYED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    FROZEN = "FROZEN"
    CANCELLED = "CANCELLED"


class BlackboardBase(DeclarativeBase):
    """Declarative base for all Foundry Blackboard tables."""


_TASK_STATUS_TYPE = Enum(
    TaskStatus,
    name="task_status",
    create_type=False,
    values_callable=lambda enum_cls: [e.value for e in enum_cls],
)


class RunRow(BlackboardBase):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False, default="planning")
    budget_usd: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    spent_usd: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0.0)
    data_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRow(BlackboardBase):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text)
    parent_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    model_profile: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="idle")
    graph_thread_id: Mapped[str | None] = mapped_column(Text)


class TaskRow(BlackboardBase):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("tasks_run_status", "run_id", "status"),
        CheckConstraint(
            "kind IN ('build', 'schema', 'delta', 'fix', 'integration', 'deps', 'verify')",
            name="ck_tasks_kind",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    wave: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    feature: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="build")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[TaskStatus] = mapped_column(
        _TASK_STATUS_TYPE, nullable=False, default=TaskStatus.PENDING
    )
    owner_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    contract_reopens: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    base_sha: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(Text)
    worktree_path: Mapped[str | None] = mapped_column(Text)
    graph_thread_id: Mapped[str | None] = mapped_column(Text)
    pending_deps: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    budget_implement_usd: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    budget_review_usd: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    spent_implement_usd: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0.0)
    spent_review_usd: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0.0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskDependencyRow(BlackboardBase):
    __tablename__ = "task_dependencies"
    __table_args__ = (CheckConstraint("kind IN ('blocks', 'join')", name="ck_task_dep_kind"),)

    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="blocks")


class TaskWriteSetRow(BlackboardBase):
    __tablename__ = "task_write_sets"
    __table_args__ = (
        CheckConstraint("mode IN ('write', 'read')", name="ck_ws_mode"),
        CheckConstraint("source IN ('planned', 'expansion')", name="ck_ws_source"),
    )

    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True, default="write")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="planned")


class WriteReservationRow(BlackboardBase):
    __tablename__ = "write_reservations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContractRow(BlackboardBase):
    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_contract_run_name"),
        CheckConstraint(
            "kind IN ('db_schema', 'openapi', 'component', 'deps', 'event')",
            name="ck_contract_kind",
        ),
        CheckConstraint(
            "status IN ('draft', 'locked', 'frozen')",
            name="ck_contract_status",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    owner_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    breaking_in_wave: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ContractVersionRow(BlackboardBase):
    __tablename__ = "contract_versions"
    __table_args__ = (
        CheckConstraint(
            "change_kind IN ('initial', 'additive', 'breaking')",
            name="ck_cv_change_kind",
        ),
    )

    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pinned_pointers: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    change_kind: Mapped[str] = mapped_column(Text, nullable=False)
    changed_elements: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    change_summary: Mapped[str | None] = mapped_column(Text)
    author_agent_id: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContractSubscriptionRow(BlackboardBase):
    __tablename__ = "contract_subscriptions"

    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    scope: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)


class ContractChangeRequestRow(BlackboardBase):
    __tablename__ = "contract_change_requests"
    __table_args__ = (
        CheckConstraint(
            "classified_as IN ('additive', 'breaking', 'pinned_violation')",
            name="ck_ccr_class",
        ),
        CheckConstraint(
            "status IN ('pending', 'batched', 'approved', 'rejected', 'escalated')",
            name="ck_ccr_status",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), nullable=False)
    requested_by: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    proposed_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    classified_as: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    batch_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DecisionRow(BlackboardBase):
    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint(
            "category IN ('strategic', 'tactical', 'operational', 'release_infra')",
            name="ck_dec_cat",
        ),
        CheckConstraint(
            "reversibility IN ('reversible', 'hard', 'irreversible')",
            name="ck_dec_rev",
        ),
        CheckConstraint("mode IN ('auto', 'notify', 'approve', 'human')", name="ck_dec_mode"),
        CheckConstraint(
            "status IN ('pending', 'auto_decided', 'resolved', 'undone', 'expired')",
            name="ck_dec_status",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    recommendation: Mapped[Any | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Float)
    reversibility: Mapped[str | None] = mapped_column(Text)
    impact: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    answer: Mapped[Any | None] = mapped_column(JSONB)
    decided_by: Mapped[str | None] = mapped_column(Text)
    graph_thread_id: Mapped[str | None] = mapped_column(Text)
    blocks_task_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ThreadRow(BlackboardBase):
    __tablename__ = "threads"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('agent_dm', 'task', 'contract', 'incident', 'human_chat', 'dispute')",
            name="ck_thread_kind",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MessageRow(BlackboardBase):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), nullable=False)
    from_id: Mapped[str] = mapped_column(Text, nullable=False)
    to_id: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NoteRow(BlackboardBase):
    __tablename__ = "notes"
    __table_args__ = (CheckConstraint("kind IN ('note', 'gotcha')", name="ck_note_kind"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    feature: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReleaseRow(BlackboardBase):
    __tablename__ = "releases"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    env: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    git_sha: Mapped[str] = mapped_column(Text, nullable=False)
    image_ref: Mapped[str | None] = mapped_column(Text)
    migration_head: Mapped[str | None] = mapped_column(Text)
    migration_kind: Mapped[str | None] = mapped_column(Text)
    db_backup_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    previous_release_id: Mapped[str | None] = mapped_column(ForeignKey("releases.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IncidentRow(BlackboardBase):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    release_id: Mapped[str | None] = mapped_column(ForeignKey("releases.id"))
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    owning_domain: Mapped[str | None] = mapped_column(Text)
    fix_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmCallRow(BlackboardBase):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(Text)
    graph_thread_id: Mapped[str | None] = mapped_column(Text)
    node: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    profile: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(Text, nullable=False, default="openai_compat")
    billing_mode: Mapped[str] = mapped_column(Text, nullable=False, default="paid_api")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    est_value_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    replayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


EVENTS_PUBLISH_SEQ = Sequence("events_publish_seq")


class EventRow(BlackboardBase):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    publish_seq: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessedEventRow(BlackboardBase):
    __tablename__ = "processed_events"

    consumer_group: Mapped[str] = mapped_column(Text, primary_key=True)
    publish_seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)


# ---------- Pydantic v2 Read DTOs (detached from live ORM sessions) ----------


class RunRecord(BaseModel):
    id: str
    project_id: str
    phase: str
    budget_usd: float
    spent_usd: float
    data_policy: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    ended_at: datetime | None = None


class AgentRecord(BaseModel):
    id: str
    run_id: str
    role: str
    domain: str | None = None
    parent_agent_id: str | None = None
    model_profile: str
    status: str
    graph_thread_id: str | None = None


class TaskRecord(BaseModel):
    id: str
    run_id: str
    wave: int
    domain: str
    feature: str | None = None
    kind: str
    title: str
    description: str
    acceptance: list[Any] = Field(default_factory=list)
    status: TaskStatus
    owner_agent_id: str | None = None
    parent_task_id: str | None = None
    attempt: int = 0
    max_attempts: int = 3
    contract_reopens: int = 0
    base_sha: str | None = None
    branch: str | None = None
    worktree_path: str | None = None
    graph_thread_id: str | None = None
    pending_deps: list[Any] = Field(default_factory=list)
    budget_implement_usd: float
    budget_review_usd: float
    spent_implement_usd: float = 0.0
    spent_review_usd: float = 0.0
    version: int = 1
    created_at: datetime
    updated_at: datetime


class ContractRecord(BaseModel):
    id: str
    run_id: str
    name: str
    domain: str
    kind: str
    owner_agent_id: str | None = None
    current_version: int = 0
    status: str = "draft"
    breaking_in_wave: int = 0
    version: int = 1


class ContractVersionRecord(BaseModel):
    contract_id: str
    version: int
    body: dict[str, Any]
    pinned_pointers: list[str] = Field(default_factory=list)
    change_kind: str
    changed_elements: list[str] = Field(default_factory=list)
    change_summary: str | None = None
    author_agent_id: str | None = None
    approved_by: str | None = None
    created_at: datetime


class ContractChangeRequestRecord(BaseModel):
    id: str
    contract_id: str
    requested_by: str
    proposed_body: dict[str, Any]
    reason: str
    classified_as: str | None = None
    status: str
    batch_id: str | None = None
    created_at: datetime


class DecisionRecord(BaseModel):
    id: str
    run_id: str
    idempotency_key: str
    category: str
    title: str
    context: str
    options: list[Any]
    recommendation: Any | None = None
    confidence: float | None = None
    reversibility: str | None = None
    impact: dict[str, Any] = Field(default_factory=dict)
    mode: str
    status: str
    answer: Any | None = None
    decided_by: str | None = None
    graph_thread_id: str | None = None
    blocks_task_ids: list[str] = Field(default_factory=list)
    version: int = 1
    created_at: datetime
    decided_at: datetime | None = None


class MessageRecord(BaseModel):
    id: str
    thread_id: str
    from_id: str
    to_id: str | None = None
    body: str
    created_at: datetime


class NoteRecord(BaseModel):
    id: str
    run_id: str
    task_id: str | None = None
    feature: str | None = None
    kind: str
    body: str
    created_at: datetime


class LlmCallRecord(BaseModel):
    id: int
    run_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    graph_thread_id: str | None = None
    node: str | None = None
    provider: str
    model: str
    profile: str
    transport: str
    billing_mode: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost_usd: float
    est_value_usd: float
    latency_ms: int | None = None
    replayed: bool = False
    prompt_hash: str | None = None
    created_at: datetime


class FeatureRow(BlackboardBase):
    __tablename__ = "features"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'verified', 'merged', 'failed')",
            name="ck_features_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    budget_blackbox_tests_usd: Mapped[float] = mapped_column(
        Numeric(8, 4), nullable=False, default=0.30
    )
    spent_blackbox_tests_usd: Mapped[float] = mapped_column(
        Numeric(8, 4), nullable=False, default=0.0
    )
    dispute_rounds: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BlackboardEvent(BaseModel):
    id: int
    publish_seq: int | None = None
    run_id: str
    type: str
    subject_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    published_at: datetime | None = None


class FeatureRecord(BaseModel):
    run_id: str
    name: str
    status: str = "pending"
    budget_blackbox_tests_usd: float = 0.30
    spent_blackbox_tests_usd: float = 0.0
    dispute_rounds: int = 0
    updated_at: datetime


class WriteReservationRecord(BaseModel):
    id: int
    run_id: str
    task_id: str
    path: str
    expires_at: datetime
    heartbeat_at: datetime
    released_at: datetime | None = None


class ReservationConflict(BaseModel):
    requested_path: str
    holder_task_id: str
    holder_path: str


class ContractSubscriptionRecord(BaseModel):
    contract_id: str
    task_id: str
    scope: list[str] = Field(default_factory=list)
