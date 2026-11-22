"""Pydantic models and feedback formatting for Foundry verification gates (G1/G2)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class GateName(StrEnum):
    """Verification gate identifiers."""

    G1 = "G1"
    G2 = "G2"


class CheckOutcome(BaseModel):
    """Result of an individual check step (linter, typechecker, tests, scanner)."""

    name: str
    passed: bool
    blocking: bool = True
    exit_code: int = 0
    duration_ms: int = 0
    detail: str = ""
    stdout: str = ""
    stderr: str = ""


class ContractViolation(BaseModel):
    """Structured violation emitted by contract, test-id, or secret checks."""

    kind: Literal[
        "pinned_violation",
        "breaking_change",
        "testid_missing",
        "schema_error",
        "secret_leak",
    ]
    pointer: str
    message: str


class GateReport(BaseModel):
    """Aggregate report for a G1 or G2 verification pass."""

    gate: GateName
    passed: bool
    checks: list[CheckOutcome] = Field(default_factory=list)
    violations: list[ContractViolation] = Field(default_factory=list)
    exported_openapi_path: str | None = None

    def summary(self) -> str:
        """Return a concise single-line summary of gate outcome."""
        blocking = [c for c in self.checks if c.blocking]
        failed_checks = [c.name for c in blocking if not c.passed]
        status = "PASSED" if self.passed else "FAILED"
        parts = [
            f"Gate {self.gate.value} {status}",
            f"checks={len(blocking) - len(failed_checks)}/{len(blocking)}",
            f"violations={len(self.violations)}",
        ]
        if failed_checks:
            parts.append(f"failed=[{', '.join(failed_checks)}]")
        return " | ".join(parts)

    def to_feedback_prompt(self, *, max_output_chars: int = 1600) -> str:
        """Render actionable failure details to feed back into an implementer loop."""
        lines = [self.summary()]
        for check in self.checks:
            if check.blocking and not check.passed:
                lines.append(
                    f"\n[FAILED CHECK: {check.name} (exit={check.exit_code})] {check.detail}"
                )
                combined = "\n".join(s for s in (check.stdout.strip(), check.stderr.strip()) if s)
                if combined:
                    if len(combined) > max_output_chars:
                        combined = f"...\n{combined[-max_output_chars:]}"
                    lines.append(combined)

        for violation in self.violations:
            lines.append(
                f"\n[VIOLATION: {violation.kind} @ {violation.pointer}] {violation.message}"
            )

        return "\n".join(lines)
