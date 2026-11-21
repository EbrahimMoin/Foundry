"""Prompt normalizer for deterministic cassettes and tool-history compaction."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

# Compile volatile patterns once
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_ULID_RE = re.compile(r"\b(?:run|t|dec|c)_[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{10,26}\b")
_ISO_TS_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_WORKTREE_RE = re.compile(
    r"(?:[A-Za-z]:\\(?:Users|tmp|var)\\[^\s\"']+|"
    r"/(?:tmp|var/tmp|home/[^/\s]+/foundry/(?:worktrees|workspaces))/[^\s\"']+)"
)
_CID_RE = re.compile(r"\bsbx_[A-Za-z0-9_-]+\b")
_SHA_RE = re.compile(
    r"\b(?:[0-9a-f]{40}|(?=[0-9a-f]{7,39}\b)(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,39})\b"
)
_TEST_TIME_RE = re.compile(r"\b(passed|failed|skipped|warnings?)\s+in\s+\d+(?:\.\d+)?s\b")


class PromptNormalizer:
    """Replaces volatile runtime tokens with stable placeholders before hashing."""

    def normalize_text(self, text: str, state: dict[str, dict[str, str]] | None = None) -> str:
        maps = state if state is not None else {"id": {}, "sha": {}}

        def _number_token(kind: str, raw: str, prefix: str) -> str:
            bucket = maps[kind]
            if raw not in bucket:
                bucket[raw] = f"<{prefix}_{len(bucket) + 1}>"
            return bucket[raw]

        out = _WORKTREE_RE.sub("<wt>", text)
        out = _CID_RE.sub("<cid>", out)
        out = _ISO_TS_RE.sub("<ts>", out)
        out = _UUID_RE.sub(lambda m: _number_token("id", m.group(0), "id"), out)
        out = _ULID_RE.sub(lambda m: _number_token("id", m.group(0), "id"), out)
        out = _TEST_TIME_RE.sub(lambda m: f"{m.group(1)} in <t>", out)
        out = _SHA_RE.sub(lambda m: _number_token("sha", m.group(0), "sha"), out)
        return out

    def normalize_value(
        self,
        value: Any,
        state: dict[str, dict[str, str]] | None = None,
    ) -> Any:
        maps = state if state is not None else {"id": {}, "sha": {}}
        if isinstance(value, str):
            return self.normalize_text(value, maps)
        if isinstance(value, list):
            return [self.normalize_value(item, maps) for item in value]
        if isinstance(value, dict):
            return {k: self.normalize_value(v, maps) for k, v in value.items()}
        return value

    def compute_hash(
        self,
        *,
        model: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] = (),
    ) -> str:
        state: dict[str, dict[str, str]] = {"id": {}, "sha": {}}
        normalized_payload = {
            "model": model,
            "messages": self.normalize_value(list(messages), state),
            "tools": self.normalize_value(list(tools), state),
        }
        canonical = json.dumps(normalized_payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compact_tool_history(
    messages: Sequence[dict[str, Any]],
    *,
    keep_recent_steps: int = 6,
) -> list[dict[str, Any]]:
    """Condense older tool-role outputs (>keep_recent_steps back) to protect prompt caches."""
    if not messages:
        return []
    tool_indices = [idx for idx, msg in enumerate(messages) if msg.get("role") == "tool"]
    if len(tool_indices) <= keep_recent_steps:
        return [dict(m) for m in messages]

    cutoff_idx = tool_indices[-keep_recent_steps]
    compacted: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        copy = dict(msg)
        if idx < cutoff_idx and copy.get("role") == "tool":
            content = str(copy.get("content", "")).strip()
            first_line = content.splitlines()[0] if content else ""
            if len(first_line) > 160:
                first_line = f"{first_line[:157]}..."
            tool_name = copy.get("name", "tool")
            copy["content"] = f"[compacted {tool_name} output]: {first_line}"
        compacted.append(copy)
    return compacted
