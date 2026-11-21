"""Deterministic outgoing secret scanner for prompts, traces, and commits."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SecretMatch:
    rule: str
    preview: str
    start: int
    end: int


class SecretLeakError(Exception):
    """Raised when an unredacted secret is detected in an outgoing payload."""

    def __init__(self, matches: Sequence[SecretMatch]) -> None:
        self.matches = list(matches)
        rules = ", ".join(sorted({m.rule for m in matches}))
        super().__init__(f"Blocked outgoing payload containing secrets ({rules})")


_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai_api_key", re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("xai_api_key", re.compile(r"\bxai-[A-Za-z0-9_-]{20,}\b")),
    ("groq_api_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----"
            r"[\s\S]*?"
            r"-----END (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----"
        ),
    ),
    (
        "bearer_jwt",
        re.compile(r"\bBearer\s+eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
)


class SecretScanner:
    """Scans and redacts API keys, tokens, and private keys before leaving the daemon."""

    def __init__(self, extra_patterns: Sequence[tuple[str, re.Pattern[str]]] = ()) -> None:
        self._patterns = (*_SECRET_PATTERNS, *extra_patterns)

    def scan_text(self, text: str) -> list[SecretMatch]:
        matches: list[SecretMatch] = []
        for rule, pattern in self._patterns:
            for found in pattern.finditer(text):
                raw = found.group(0)
                preview = f"{raw[:6]}...{raw[-4:]}" if len(raw) > 12 else "***"
                matches.append(
                    SecretMatch(
                        rule=rule,
                        preview=preview,
                        start=found.start(),
                        end=found.end(),
                    )
                )
        return matches

    def redact_text(self, text: str) -> str:
        redacted = text
        for rule, pattern in self._patterns:
            redacted = pattern.sub(f"<REDACTED:{rule}>", redacted)
        return redacted

    def scan_messages(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] = (),
    ) -> list[SecretMatch]:
        payload = {"messages": list(messages), "tools": list(tools)}
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return self.scan_text(serialized)

    def assert_clean(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] = (),
    ) -> None:
        matches = self.scan_messages(messages, tools)
        if matches:
            raise SecretLeakError(matches)
