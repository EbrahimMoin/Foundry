"""ContractService managing staged drafts, change classification, and v1 locking."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from foundry.blackboard.models import ContractRecord, ContractVersionRecord
from foundry.blackboard.repository import BlackboardRepository, NotFoundError
from foundry.contracts.models import ComponentContract, ContractValidationResult, DbSchemaContract
from foundry.contracts.validator import validate_staged_contracts
from foundry.verification.contract_checks import check_pinned_pointers, diff_openapi_breaking
from foundry.verification.models import ContractViolation


class ContractValidationError(ValueError):
    """Raised when staged contracts fail validation before lock."""

    def __init__(self, result: ContractValidationResult) -> None:
        self.result = result
        summary = "; ".join(f"[{v.kind}] {v.pointer}: {v.message}" for v in result.violations)
        super().__init__(f"Staged contract validation failed: {summary}")


class ContractService:
    """Persists versioned contracts on Postgres Blackboard and enforces lock & change rules."""

    def __init__(self, repo: BlackboardRepository) -> None:
        self._repo = repo

    async def save_draft(
        self,
        *,
        run_id: str,
        name: str,
        domain: str,
        kind: str,
        body: dict[str, Any],
        author_agent_id: str | None = None,
        pinned_pointers: Sequence[str] = (),
        change_kind: str = "initial",
        changed_elements: Sequence[str] = (),
        change_summary: str | None = None,
    ) -> tuple[ContractRecord, ContractVersionRecord]:
        return await self._repo.save_contract_version(
            run_id=run_id,
            name=name,
            domain=domain,
            kind=kind,
            body=body,
            author_agent_id=author_agent_id,
            pinned_pointers=pinned_pointers,
            change_kind=change_kind,
            changed_elements=changed_elements,
            change_summary=change_summary,
        )

    async def load_latest_body(self, run_id: str, name: str) -> dict[str, Any]:
        ctr = await self._repo.get_contract(run_id, name)
        ver = await self._repo.get_latest_contract_version(ctr.id)
        return dict(ver.body)

    async def propose_change(
        self,
        *,
        contract_id: str,
        requested_by_task_id: str,
        proposed_body: dict[str, Any],
        reason: str,
        pinned_openapi: dict[str, Any] | None = None,
    ) -> tuple[str, list[ContractViolation]]:
        """Classify a contract change request and store it on the Blackboard."""
        contract = await self._repo.get_contract_by_id(contract_id)
        current_ver = await self._repo.get_latest_contract_version(contract_id)
        violations: list[ContractViolation] = []

        if contract.kind == "openapi":
            if pinned_openapi is not None:
                violations.extend(check_pinned_pointers(pinned_openapi, proposed_body))
            elif current_ver.pinned_pointers:
                pinned_set = set(current_ver.pinned_pointers)
                for v in check_pinned_pointers(current_ver.body, proposed_body):
                    if any(
                        v.pointer == p
                        or v.pointer.startswith(f"{p}/")
                        or p.startswith(f"{v.pointer}/")
                        for p in pinned_set
                    ):
                        violations.append(v)

            if violations:
                await self._repo.create_contract_change_request(
                    contract_id=contract_id,
                    requested_by=requested_by_task_id,
                    proposed_body=proposed_body,
                    reason=reason,
                    classified_as="pinned_violation",
                    status="rejected",
                )
                return "pinned_violation", violations

            breaking = diff_openapi_breaking(current_ver.body, proposed_body)
            classified = "breaking" if breaking else "additive"
        else:
            breaking = []
            classified = "additive"

        ccr_status = "approved" if classified == "additive" else "pending"
        await self._repo.create_contract_change_request(
            contract_id=contract_id,
            requested_by=requested_by_task_id,
            proposed_body=proposed_body,
            reason=reason,
            classified_as=classified,
            status=ccr_status,
        )
        if classified == "additive":
            await self._repo.save_contract_version(
                run_id=contract.run_id,
                name=contract.name,
                domain=contract.domain,
                kind=contract.kind,
                body=proposed_body,
                change_kind="additive",
                change_summary=reason,
            )
        return classified, breaking

    async def lock_all_v1(
        self,
        *,
        run_id: str,
        pinned_openapi: dict[str, Any] | None = None,
        pinned_testids: Sequence[str] = (),
    ) -> ContractValidationResult:
        """Validate staged contracts jointly and lock v1, emitting `contracts.locked`."""
        contracts = await self._repo.list_contracts(run_id)
        by_name = {c.name: c for c in contracts}
        missing = [n for n in ("db_schema", "api", "ui_components", "deps") if n not in by_name]
        if missing:
            raise NotFoundError(f"Missing staged contracts for {run_id}: {', '.join(missing)}")

        db_ver = await self._repo.get_latest_contract_version(by_name["db_schema"].id)
        api_ver = await self._repo.get_latest_contract_version(by_name["api"].id)
        ui_ver = await self._repo.get_latest_contract_version(by_name["ui_components"].id)

        db_schema = DbSchemaContract.model_validate(db_ver.body)
        ui_components = ComponentContract.model_validate(ui_ver.body)
        result = validate_staged_contracts(
            db_schema=db_schema,
            openapi_doc=api_ver.body,
            components=ui_components,
            pinned_openapi=pinned_openapi,
            pinned_testids=pinned_testids,
        )
        if not result.valid:
            raise ContractValidationError(result)

        locked_names: list[str] = []
        for name in ("db_schema", "api", "ui_components", "deps"):
            ctr = by_name[name]
            if ctr.status != "locked":
                await self._repo.lock_contract(ctr.id, ctr.version, status="locked")
            locked_names.append(name)

        await self._repo.emit_event(
            run_id=run_id,
            event_type="contracts.locked",
            subject_id=run_id,
            payload={"run_id": run_id, "version": 1, "contracts": locked_names},
        )
        return result
