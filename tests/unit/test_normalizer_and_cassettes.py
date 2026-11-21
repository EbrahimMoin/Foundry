"""Unit tests for PromptNormalizer, tool history compaction, and CassetteStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from foundry.config import BillingMode, TransportKind
from foundry.gateway import (
    CassetteMissError,
    CassetteStore,
    GatewayResponse,
    MissPolicy,
    PromptNormalizer,
    compact_tool_history,
)


def test_prompt_normalizer_produces_identical_hash_for_volatile_inputs() -> None:
    normalizer = PromptNormalizer()
    msg_a = [
        {
            "role": "user",
            "content": (
                "Task t_01JC9X7K2M3N4P5Q6R7S8T9V0W at 2026-11-21T12:00:00Z "
                "in /home/ebrah/foundry/worktrees/t_01JC9X7K2M3N4P5Q6R7S8T9V0W "
                "container sbx_task1 git sha deadbeef1234 "
                "UUID 123e4567-e89b-12d3-a456-426614174000 "
                "3 passed in 0.42s"
            ),
        }
    ]
    msg_b = [
        {
            "role": "user",
            "content": (
                "Task t_01ZZ9X7K2M3N4P5Q6R7S8T9V0A at 2026-12-01T08:30:15.123Z "
                "in C:\\Users\\ebrah\\worktree_abc "
                "container sbx_task99 git sha cafe98765432 "
                "UUID 999e4567-e89b-12d3-a456-426614174999 "
                "3 passed in 4.91s"
            ),
        }
    ]

    hash_a = normalizer.compute_hash(model="gemini-2.5-flash-lite", messages=msg_a)
    hash_b = normalizer.compute_hash(model="gemini-2.5-flash-lite", messages=msg_b)
    assert hash_a == hash_b


def test_prompt_normalizer_preserves_plain_numbers_and_english_hex_words() -> None:
    normalizer = PromptNormalizer()
    sample = "Order 1234567 uses faceted UI and defaced header next to commit deadbeef1234"
    normalized = normalizer.normalize_text(sample)
    assert "1234567" in normalized
    assert "faceted" in normalized
    assert "defaced" in normalized
    assert "<sha_1>" in normalized
    full_sha = "a" * 40
    assert "<sha_1>" in normalizer.normalize_text(f"HEAD is {full_sha}")


def test_compact_tool_history_preserves_recent_steps() -> None:
    messages = [{"role": "system", "content": "sys"}]
    for i in range(8):
        messages.append(
            {
                "role": "tool",
                "name": f"tool_{i}",
                "content": f"line 1 of step {i}\n" + ("verbose\n" * 20),
            }
        )

    compacted = compact_tool_history(messages, keep_recent_steps=6)
    # 2 older tool messages compacted, 6 recent kept verbatim
    assert compacted[1]["content"].startswith("[compacted tool_0 output]: line 1 of step 0")
    assert compacted[2]["content"].startswith("[compacted tool_1 output]: line 1 of step 1")
    assert "verbose" in compacted[3]["content"]
    assert "verbose" in compacted[-1]["content"]


def test_cassette_store_save_and_strict_replay(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path, miss_policy=MissPolicy.STRICT)
    sample = GatewayResponse(
        provider_id="gemini_paid",
        model="gemini-2.5-flash-lite",
        transport=TransportKind.GOOGLE_GENAI,
        billing_mode=BillingMode.PAID_API,
        content="Implemented feature cleanly.",
        tokens_in=100,
        tokens_cached=60,
        tokens_out=25,
        cost_usd=0.001,
        est_value_usd=0.001,
    )
    store.save(
        sample,
        prompt_hash="abc123",
        role="implementer",
        task_slug="bookmarks",
        node="act",
        step_index=2,
    )

    by_hash = store.lookup(prompt_hash="abc123", role="implementer")
    assert by_hash is not None
    assert by_hash.replayed is True
    assert by_hash.cost_usd == 0.0
    assert by_hash.content == "Implemented feature cleanly."

    by_seq = store.lookup(
        prompt_hash="different_hash",
        role="implementer",
        task_slug="bookmarks",
        node="act",
        step_index=2,
    )
    assert by_seq is not None
    assert by_seq.replayed is True
    assert by_seq.cost_usd == 0.0

    missing = store.lookup(
        prompt_hash="missing",
        role="implementer",
        task_slug="other",
        node="act",
        step_index=9,
    )
    assert missing is None
    assert MissPolicy.STRICT == "strict"
    with pytest.raises(CassetteMissError):
        raise CassetteMissError("strict check")
