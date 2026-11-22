"""RunSupervisor coordinating independent LangGraph threads, EventRelay, Decisions & Waves."""

from __future__ import annotations

import asyncio
import base64
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from foundry.agents.graphs.lead_graph import run_staged_contract_planning
from foundry.agents.graphs.project_graph import (
    ProjectGraphDeps,
    ProjectState,
    build_project_graph,
)
from foundry.blackboard.events import EventDispatcher, EventRelay
from foundry.blackboard.models import ContractRecord, DecisionRecord, TaskRecord, TaskStatus
from foundry.blackboard.repository import BlackboardRepository
from foundry.config.loader import default_models_config
from foundry.config.schemas import (
    AutonomyConfig,
    BudgetConfig,
    DataPolicyConfig,
    ModelsConfig,
)
from foundry.contracts.service import ContractService
from foundry.decisions.service import DecisionService
from foundry.gateway.cassette import CassetteStore
from foundry.gateway.router import ModelGateway, RunBudgetState
from foundry.stack_packs import FastAPIReactPostgresPack, StackPack
from foundry.supervisor.waves import (
    advance_ready_tasks,
    materialize_wave_tasks,
    plan_wave1_foundation,
    plan_wave2_features,
)

TaskExecutor = Callable[[TaskRecord, dict[str, Any]], Awaitable[None]]


class RunStateSummary(BaseModel):
    """Structured snapshot returned by `RunSupervisor.start_run` and `resume_run`."""

    run_id: str
    status: str  # waiting_decision | paused_wave | completed
    phase: str
    pending_decisions: list[DecisionRecord] = Field(default_factory=list)
    contracts: list[ContractRecord] = Field(default_factory=list)
    tasks: list[TaskRecord] = Field(default_factory=list)
    requirements: dict[str, Any] | None = None
    architecture: dict[str, Any] | None = None


async def _default_task_executor(task: TaskRecord, context: dict[str, Any]) -> None:
    """Default deterministic P3 wave executor transitioning READY -> MERGED cleanly."""
    _ = (task, context)


class RunSupervisor:
    """Coordinates Postgres Blackboard, EventRelay, Decisions, Domain Leads & Two-Wave build."""

    def __init__(
        self,
        repo: BlackboardRepository,
        *,
        models_cfg: ModelsConfig | None = None,
        autonomy_cfg: AutonomyConfig | None = None,
        budget_cfg: BudgetConfig | None = None,
        data_policy: DataPolicyConfig | None = None,
        deploy_target: str = "local_compose",
        stack_pack: StackPack | None = None,
        gateway: ModelGateway | None = None,
        cassettes: CassetteStore | None = None,
        checkpointer: Any | None = None,
        adr_dir: Path | None = None,
        task_executor: TaskExecutor | None = None,
        auto_execute_waves: bool = True,
    ) -> None:
        self.repo = repo
        self.models_cfg = models_cfg or default_models_config()
        self.autonomy_cfg = autonomy_cfg or AutonomyConfig()
        self.budget_cfg = budget_cfg or BudgetConfig()
        self.data_policy = data_policy or DataPolicyConfig()
        self.deploy_target = deploy_target
        self.stack_pack: StackPack = stack_pack or FastAPIReactPostgresPack()
        self._explicit_gateway = gateway
        self._cassettes = cassettes
        self.checkpointer = checkpointer if checkpointer is not None else MemorySaver()
        self.decisions = DecisionService(
            repo,
            autonomy_cfg=self.autonomy_cfg,
            deploy_target=self.deploy_target,
            adr_dir=adr_dir,
        )
        self.contracts = ContractService(repo)
        self.dispatcher = EventDispatcher(repo)
        self.relay = EventRelay(repo, self.dispatcher)
        self.task_executor = task_executor or _default_task_executor
        self.auto_execute_waves = auto_execute_waves

        self._run_meta: dict[str, dict[str, Any]] = {}
        self._wave1_key_maps: dict[str, dict[str, str]] = {}

    def _build_gateway(self) -> tuple[ModelGateway, bool]:
        if self._explicit_gateway is not None:
            return self._explicit_gateway, False
        gw = ModelGateway(
            self.models_cfg,
            data_policy=self.data_policy,
            cassettes=self._cassettes,
            run_budget=RunBudgetState(self.budget_cfg),
        )
        return gw, True

    async def ensure_contract_planning(self, run_id: str, state: dict[str, Any]) -> int | None:
        """Idempotent hook executing staged Data -> Backend -> Frontend || Infra contracts."""
        existing = await self.repo.list_contracts(run_id)
        if len(existing) == 4 and all(c.status == "locked" for c in existing):
            return 1

        meta = self._run_meta.get(run_id, {})
        gateway, owned = self._build_gateway()
        try:
            await run_staged_contract_planning(
                run_id=run_id,
                task_slug=str(state.get("task_slug") or meta.get("task_slug") or "app"),
                spec=str(state.get("spec") or ""),
                requirements=dict(state.get("requirements") or {}),
                architecture=dict(state.get("architecture") or {}),
                gateway=gateway,
                blackboard=self.repo,
                contracts_service=self.contracts,
                pinned_openapi=meta.get("pinned_openapi"),
                pinned_openapi_yaml=meta.get("pinned_openapi_yaml"),
                pinned_testids=meta.get("pinned_testids", ()),
                pinned_testids_yaml=meta.get("pinned_testids_yaml"),
                checkpointer=self.checkpointer,
            )
            await self.relay.flush_once()
        finally:
            if owned:
                await gateway.aclose()
        return 1

    async def _run_wave_tasks(self, run_id: str, *, wave: int) -> bool:
        contract_map: dict[str, dict[str, Any]] = {}
        for ctr in await self.repo.list_contracts(run_id):
            ver = await self.repo.get_latest_contract_version(ctr.id)
            contract_map[ctr.name] = ver.body

        while True:
            await advance_ready_tasks(self.repo, run_id)
            wave_tasks = await self.repo.list_tasks(run_id, wave=wave)
            if wave_tasks and all(t.status == TaskStatus.MERGED for t in wave_tasks):
                await self.repo.emit_event(
                    run_id=run_id,
                    event_type="wave.completed",
                    subject_id=run_id,
                    payload={"run_id": run_id, "wave": wave},
                )
                await self.relay.flush_once()
                return True

            ready_tasks = [t for t in wave_tasks if t.status == TaskStatus.READY]
            if not ready_tasks:
                return False

            for ready_task in ready_tasks:
                t_prog = await self.repo.set_task_status(
                    ready_task.id,
                    TaskStatus.IN_PROGRESS,
                    ready_task.version,
                )
                await self.task_executor(t_prog, {"contracts": contract_map, "wave": wave})
                t_rev = await self.repo.set_task_status(
                    t_prog.id,
                    TaskStatus.IN_REVIEW,
                    t_prog.version,
                )
                t_ver = await self.repo.set_task_status(
                    t_rev.id,
                    TaskStatus.VERIFIED,
                    t_rev.version,
                )
                t_q = await self.repo.set_task_status(
                    t_ver.id,
                    TaskStatus.QUEUED,
                    t_ver.version,
                )
                await self.repo.set_task_status(
                    t_q.id,
                    TaskStatus.MERGED,
                    t_q.version,
                )
            await self.relay.flush_once()

    async def ensure_wave1(self, run_id: str, state: dict[str, Any]) -> bool:
        """Idempotent Wave 1 Foundation planner and optional wave runner."""
        existing = await self.repo.list_tasks(run_id, wave=1)
        if not existing:
            planned = plan_wave1_foundation(
                app_name=str(state.get("app_name", "app")),
                stack_pack=self.stack_pack,
            )
            _, mapping = await materialize_wave_tasks(
                self.repo,
                run_id=run_id,
                planned_tasks=planned,
                budget_cfg=self.budget_cfg,
            )
            self._wave1_key_maps[run_id] = mapping
            await self.relay.flush_once()
        if not self.auto_execute_waves:
            wave1 = await self.repo.list_tasks(run_id, wave=1)
            return bool(wave1) and all(t.status == TaskStatus.MERGED for t in wave1)
        return await self._run_wave_tasks(run_id, wave=1)

    async def ensure_wave2(self, run_id: str, state: dict[str, Any]) -> bool:
        """Idempotent Wave 2 Feature planner enforcing hard Wave 1 MERGED prerequisite."""
        wave1_tasks = await self.repo.list_tasks(run_id, wave=1)
        if not wave1_tasks or any(t.status != TaskStatus.MERGED for t in wave1_tasks):
            raise RuntimeError(
                f"Wave 2 blocked for {run_id}: all Wave 1 Foundation tasks must be MERGED"
            )

        existing = await self.repo.list_tasks(run_id, wave=2)
        if not existing:
            api_body: dict[str, Any] | None = None
            try:
                api_body = await self.contracts.load_latest_body(run_id, "api")
            except Exception:
                api_body = None

            key_map = self._wave1_key_maps.get(run_id)
            if not key_map:
                sorted_w1 = sorted(wave1_tasks, key=lambda t: t.created_at)
                key_map = {f"F{idx}": t.id for idx, t in enumerate(sorted_w1, start=1)}

            planned = plan_wave2_features(
                requirements=state.get("requirements"),
                architecture=state.get("architecture"),
                api_contract=api_body,
                wave1_keys=list(key_map.keys()),
                stack_pack=self.stack_pack,
            )
            await materialize_wave_tasks(
                self.repo,
                run_id=run_id,
                planned_tasks=planned,
                budget_cfg=self.budget_cfg,
                key_to_id=key_map,
            )
            await self.relay.flush_once()
        if not self.auto_execute_waves:
            wave2 = await self.repo.list_tasks(run_id, wave=2)
            return bool(wave2) and all(t.status == TaskStatus.MERGED for t in wave2)
        return await self._run_wave_tasks(run_id, wave=2)

    async def _build_summary(
        self,
        run_id: str,
        *,
        graph_output: dict[str, Any] | None = None,
    ) -> RunStateSummary:
        run_rec = await self.repo.get_run(run_id)
        pending = await self.repo.list_decisions(run_id=run_id, status="pending")
        contracts = await self.repo.list_contracts(run_id)
        tasks = await self.repo.list_tasks(run_id)
        if pending:
            status = "waiting_decision"
        elif run_rec.phase == "done":
            status = "completed"
        else:
            status = "paused_wave"
        out = graph_output or {}
        return RunStateSummary(
            run_id=run_id,
            status=status,
            phase=run_rec.phase,
            pending_decisions=pending,
            contracts=contracts,
            tasks=tasks,
            requirements=out.get("requirements"),
            architecture=out.get("architecture"),
        )

    async def start_run(
        self,
        *,
        spec: str,
        app_name: str = "app",
        task_slug: str | None = None,
        pinned_openapi: dict[str, Any] | None = None,
        pinned_openapi_yaml: str | None = None,
        pinned_testids: tuple[str, ...] | list[str] = (),
        pinned_testids_yaml: str | None = None,
        run_id: str | None = None,
    ) -> RunStateSummary:
        """Create a run on Postgres Blackboard and advance `{run}:project` until pause or done."""
        slug = task_slug or app_name
        run_rec = await self.repo.create_run(
            project_id=app_name,
            budget_usd=self.budget_cfg.per_run_usd,
            data_policy=self.data_policy.model_dump(mode="json"),
            run_id=run_id,
            phase="planning",
        )
        self._run_meta[run_rec.id] = {
            "task_slug": slug,
            "pinned_openapi": pinned_openapi,
            "pinned_openapi_yaml": pinned_openapi_yaml,
            "pinned_testids": list(pinned_testids),
            "pinned_testids_yaml": pinned_testids_yaml,
        }

        gateway, owned = self._build_gateway()
        try:
            deps = ProjectGraphDeps(
                gateway=gateway,
                decisions=self.decisions,
                blackboard=self.repo,
                supervisor_hooks=self,
            )
            graph = build_project_graph(deps, checkpointer=self.checkpointer)
            cfg = {"configurable": {"thread_id": f"{run_rec.id}:project"}}
            init_state: ProjectState = {
                "run_id": run_rec.id,
                "task_slug": slug,
                "app_name": app_name,
                "spec": spec,
                "req_revision": 0,
                "arch_revision": 0,
                "phase": "planning",
            }
            output = await graph.ainvoke(init_state, cfg)
            await self.relay.flush_once()
        finally:
            if owned:
                await gateway.aclose()

        return await self._build_summary(run_rec.id, graph_output=output)

    async def resume_run(
        self,
        run_id: str,
        *,
        pinned_openapi: dict[str, Any] | None = None,
        pinned_openapi_yaml: str | None = None,
        pinned_testids: tuple[str, ...] | list[str] = (),
        pinned_testids_yaml: str | None = None,
    ) -> RunStateSummary:
        """Resume a checkpointed `{run}:project` thread after human decision resolution."""
        if run_id not in self._run_meta:
            run_rec = await self.repo.get_run(run_id)
            self._run_meta[run_id] = {
                "task_slug": run_rec.project_id,
                "pinned_openapi": pinned_openapi,
                "pinned_openapi_yaml": pinned_openapi_yaml,
                "pinned_testids": list(pinned_testids),
                "pinned_testids_yaml": pinned_testids_yaml,
            }

        gateway, owned = self._build_gateway()
        try:
            deps = ProjectGraphDeps(
                gateway=gateway,
                decisions=self.decisions,
                blackboard=self.repo,
                supervisor_hooks=self,
            )
            graph = build_project_graph(deps, checkpointer=self.checkpointer)
            cfg = {"configurable": {"thread_id": f"{run_id}:project"}}
            snapshot = await graph.aget_state(cfg)
            resume_payload: Any = True

            for interrupt_obj in getattr(snapshot, "interrupts", ()) or ():
                val = getattr(interrupt_obj, "value", None)
                if isinstance(val, dict) and "decision_id" in val:
                    dec = await self.repo.get_decision(str(val["decision_id"]))
                    if dec.status != "resolved":
                        return await self._build_summary(run_id)
                    resume_payload = dec.answer
                    break
                if isinstance(val, dict) and val.get("wait_for") == "contracts.locked":
                    resume_payload = {"version": 1}
                    break
                if isinstance(val, dict) and val.get("wait_for") == "wave.completed":
                    resume_payload = {"wave": val.get("wave", 1)}
                    break

            output = await graph.ainvoke(Command(resume=resume_payload), cfg)
            await self.relay.flush_once()

            # Continue automatically across consecutive auto-decided/staged phases until pause/done
            for _ in range(4):
                snap = await graph.aget_state(cfg)
                ints = getattr(snap, "interrupts", ()) or ()
                if not ints:
                    break
                val = getattr(ints[0], "value", None)
                if isinstance(val, dict) and "decision_id" in val:
                    dec = await self.repo.get_decision(str(val["decision_id"]))
                    if dec.status == "resolved":
                        output = await graph.ainvoke(Command(resume=dec.answer), cfg)
                        await self.relay.flush_once()
                        continue
                break
        finally:
            if owned:
                await gateway.aclose()

        return await self._build_summary(run_id, graph_output=output)


class JsonFileCheckpointSaver(MemorySaver):
    """JSON-persisted InMemorySaver (zero pickle) for offline multi-process CLI runs."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        if self._path.is_file():
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        for tid, ns_map in raw.get("storage", {}).items():
            for ns, ckpts in ns_map.items():
                for cid, entry in ckpts.items():
                    c_pair, m_pair, parent = entry
                    self.storage[tid][ns][cid] = (
                        (c_pair[0], base64.b64decode(c_pair[1])),
                        (m_pair[0], base64.b64decode(m_pair[1])),
                        parent,
                    )
        for item in raw.get("blobs", []):
            tid, ns, key, ver, b_type, b_b64 = item
            self.blobs[(tid, ns, key, ver)] = (b_type, base64.b64decode(b_b64))
        for item in raw.get("writes", []):
            tid, ns, cid, task_id, idx, ch, w_pair, tpath = item
            self.writes[(tid, ns, cid)][(task_id, idx)] = (
                task_id,
                ch,
                (w_pair[0], base64.b64decode(w_pair[1])),
                tpath,
            )

    def _save_to_disk(self) -> None:
        storage_dump: dict[str, dict[str, dict[str, list[Any]]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for tid, ns_map in self.storage.items():
            for ns, ckpts in ns_map.items():
                for cid, entry in ckpts.items():
                    c_pair, m_pair, parent = entry
                    storage_dump[tid][ns][cid] = [
                        [c_pair[0], base64.b64encode(c_pair[1]).decode("ascii")],
                        [m_pair[0], base64.b64encode(m_pair[1]).decode("ascii")],
                        parent,
                    ]
        blobs_dump = [
            [tid, ns, key, ver, b_type, base64.b64encode(b_bytes).decode("ascii")]
            for (tid, ns, key, ver), (b_type, b_bytes) in self.blobs.items()
        ]
        writes_dump = []
        for (tid, ns, cid), inner in self.writes.items():
            for (_task_id, idx), (t_id, ch, w_pair, tpath) in inner.items():
                writes_dump.append(
                    [
                        tid,
                        ns,
                        cid,
                        t_id,
                        idx,
                        ch,
                        [w_pair[0], base64.b64encode(w_pair[1]).decode("ascii")],
                        tpath,
                    ]
                )
        payload = {"storage": storage_dump, "blobs": blobs_dump, "writes": writes_dump}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload), encoding="utf-8")

    def put(
        self,
        config: Any,
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> Any:
        out = super().put(config, checkpoint, metadata, new_versions)
        self._save_to_disk()
        return out

    def put_writes(
        self,
        config: Any,
        writes: Any,
        task_id: str,
        task_path: str = "",
    ) -> None:
        super().put_writes(config, writes, task_id, task_path=task_path)
        self._save_to_disk()


def create_postgres_saver_context(dsn: str) -> Any:
    """Return `AsyncPostgresSaver.from_conn_string` async context manager (psycopg DSN)."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from foundry.blackboard.dsn import to_psycopg_dsn

    return AsyncPostgresSaver.from_conn_string(to_psycopg_dsn(dsn))


__all__ = [
    "JsonFileCheckpointSaver",
    "RunStateSummary",
    "RunSupervisor",
    "TaskExecutor",
    "asyncio",
    "create_postgres_saver_context",
]
