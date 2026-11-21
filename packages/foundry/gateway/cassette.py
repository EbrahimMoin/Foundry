"""Normalized record/replay cassette store for $0 engine testing."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from foundry.gateway.protocol import GatewayResponse


class MissPolicy(StrEnum):
    STRICT = "strict"
    RECORD = "record"
    PASSTHROUGH = "passthrough"


class CassetteMissError(Exception):
    """Raised when miss_policy='strict' and no matching cassette entry exists."""


class CassetteStore:
    """Stores and replays normalized GatewayResponse fixtures by content hash or sequence."""

    def __init__(
        self,
        cassette_dir: Path,
        *,
        miss_policy: MissPolicy = MissPolicy.STRICT,
    ) -> None:
        self.cassette_dir = cassette_dir
        self.miss_policy = miss_policy

    @staticmethod
    def _seq_key(
        role: str,
        task_slug: str | None,
        node: str | None,
        step_index: int,
    ) -> str:
        slug = task_slug or "default"
        node_name = node or "default"
        return f"{role}__{slug}__{node_name}__{step_index}"

    def _content_path(self, prompt_hash: str) -> Path:
        return self.cassette_dir / "by_hash" / f"{prompt_hash}.json"

    def _sequential_path(
        self,
        role: str,
        task_slug: str | None,
        node: str | None,
        step_index: int,
    ) -> Path:
        key = self._seq_key(role, task_slug, node, step_index)
        return self.cassette_dir / "by_seq" / f"{key}.json"

    def lookup(
        self,
        *,
        prompt_hash: str,
        role: str,
        task_slug: str | None = None,
        node: str | None = None,
        step_index: int = 0,
    ) -> GatewayResponse | None:
        for candidate in (
            self._content_path(prompt_hash),
            self._sequential_path(role, task_slug, node, step_index),
        ):
            if candidate.exists():
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                resp = GatewayResponse.model_validate(raw)
                return resp.model_copy(
                    update={"replayed": True, "cost_usd": 0.0, "est_value_usd": 0.0}
                )
        return None

    def save(
        self,
        response: GatewayResponse,
        *,
        prompt_hash: str,
        role: str,
        task_slug: str | None = None,
        node: str | None = None,
        step_index: int = 0,
    ) -> None:
        payload = json.dumps(response.model_dump(mode="json"), indent=2)
        for candidate in (
            self._content_path(prompt_hash),
            self._sequential_path(role, task_slug, node, step_index),
        ):
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(payload, encoding="utf-8")
