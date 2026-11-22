"""Daemon-hosted `blackboard-mcp` server exposing exact JSON rows and safe coordination tools."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from foundry.blackboard.models import (
    ContractRecord,
    ContractVersionRecord,
    DecisionRecord,
    MessageRecord,
    NoteRecord,
    TaskRecord,
)
from foundry.blackboard.repository import BlackboardRepository
from foundry.contracts.service import ContractService


class BlackboardMCPServer:
    """Exposes exact-JSON Blackboard reads, notes/DMs, and contract change proposals."""

    def __init__(
        self,
        repo: BlackboardRepository,
        contracts: ContractService | None = None,
    ) -> None:
        self._repo = repo
        self._contracts = contracts or ContractService(repo)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        task: TaskRecord = await self._repo.get_task(task_id)
        write_set = await self._repo.get_task_write_sets(task_id)
        deps = await self._repo.get_task_dependencies(task_id)
        return {
            **task.model_dump(mode="json"),
            "write_set": write_set,
            "depends_on": deps,
        }

    async def get_contract(self, run_id: str, name: str) -> dict[str, Any]:
        ctr: ContractRecord = await self._repo.get_contract(run_id, name)
        ver: ContractVersionRecord = await self._repo.get_latest_contract_version(ctr.id)
        return {
            "contract": ctr.model_dump(mode="json"),
            "version": ver.model_dump(mode="json"),
        }

    async def list_decisions(self, run_id: str) -> list[dict[str, Any]]:
        rows: list[DecisionRecord] = await self._repo.list_decisions(run_id=run_id)
        return [r.model_dump(mode="json") for r in rows]

    async def post_note(
        self,
        run_id: str,
        kind: Literal["note", "gotcha"],
        body: str,
        task_id: str | None = None,
        feature: str | None = None,
    ) -> dict[str, Any]:
        note: NoteRecord = await self._repo.post_note(
            run_id=run_id,
            kind=kind,
            body=body,
            task_id=task_id,
            feature=feature,
        )
        return note.model_dump(mode="json")

    async def post_message(
        self,
        run_id: str,
        thread_id: str,
        from_id: str,
        body: str,
        to_id: str | None = None,
    ) -> dict[str, Any]:
        await self._repo.ensure_thread(run_id=run_id, kind="agent_dm", thread_id=thread_id)
        msg: MessageRecord = await self._repo.post_message(
            thread_id=thread_id,
            from_id=from_id,
            body=body,
            to_id=to_id,
        )
        return msg.model_dump(mode="json")

    async def propose_contract_change(
        self,
        contract_id: str,
        requested_by_task_id: str,
        proposed_body: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        classification, issues = await self._contracts.propose_change(
            contract_id=contract_id,
            requested_by_task_id=requested_by_task_id,
            proposed_body=proposed_body,
            reason=reason,
        )
        return {
            "classified_as": classification,
            "violations": [v.model_dump(mode="json") for v in issues],
        }

    def build_mcp_server(self) -> MCPServer:
        """Build official mcp v2 server registering blackboard-mcp tools."""
        server = MCPServer("foundry-blackboard-mcp")

        @server.tool(description="Fetch exact Postgres Task JSON, write set, and dependencies.")
        async def get_task(task_id: str) -> dict[str, Any]:
            return await self.get_task(task_id)

        @server.tool(description="Fetch exact latest contract JSON from Postgres Blackboard.")
        async def get_contract(run_id: str, name: str) -> dict[str, Any]:
            return await self.get_contract(run_id, name)

        @server.tool(description="List decisions for a run from Postgres Blackboard.")
        async def list_decisions(run_id: str) -> list[dict[str, Any]]:
            return await self.list_decisions(run_id)

        @server.tool(description="Record a scoped note or gotcha linked to a task or feature.")
        async def post_note(
            run_id: str,
            kind: Literal["note", "gotcha"],
            body: str,
            task_id: str | None = None,
            feature: str | None = None,
        ) -> dict[str, Any]:
            return await self.post_note(run_id, kind, body, task_id=task_id, feature=feature)

        @server.tool(description="Post a direct agent message into a Postgres Blackboard thread.")
        async def post_message(
            run_id: str,
            thread_id: str,
            from_id: str,
            body: str,
            to_id: str | None = None,
        ) -> dict[str, Any]:
            return await self.post_message(run_id, thread_id, from_id, body, to_id=to_id)

        @server.tool(
            description="Propose and classify a contract change (pinned/additive/breaking)."
        )
        async def propose_contract_change(
            contract_id: str,
            requested_by_task_id: str,
            proposed_body: dict[str, Any],
            reason: str,
        ) -> dict[str, Any]:
            return await self.propose_contract_change(
                contract_id, requested_by_task_id, proposed_body, reason
            )

        return server
