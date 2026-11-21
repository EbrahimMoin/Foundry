"""StackPack Protocol defining conventions, skeleton scaffolding, and check commands."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from foundry.sandbox.protocol import PackageInstallPolicy


@runtime_checkable
class StackPack(Protocol):
    """Plugin interface for stack-specific templates, conventions, and checks."""

    name: str
    sandbox_image: str

    def default_write_set(
        self,
        domain: str,
        feature: str | None = None,
        kind: str = "build",
    ) -> list[str]: ...

    def check_commands(
        self,
        domain: str,
        write_set: Sequence[str] = (),
    ) -> list[list[str]]: ...

    def curated_package_policy(self) -> PackageInstallPolicy: ...

    def scaffold(self, target_dir: Path, app_name: str) -> list[Path]: ...
