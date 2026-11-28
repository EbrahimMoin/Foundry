"""Opt-in single-shot non-tool OpenAI Codex CLI transport (A7-2 compliant).

Invokes the user's own officially installed and authenticated `codex` CLI via
`codex exec --skip-git-repo-check --sandbox read-only -m <model> -`.

Strict guardrails (`AGENTS.md` Section 3.1 A7-2 & Design Doc Section 16.3):
- NEVER reads, scrapes, parses, or proxies `~/.codex/auth.json` or any OAuth files.
- NEVER supports multi-step tool-calling ReAct loops (`supports_tool_calling=False`).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import Any

from foundry.config.schemas import BillingMode, ProviderConfig, TransportKind
from foundry.gateway.protocol import (
    GatewayRequest,
    GatewayResponse,
    TransportError,
    TransportHealth,
)


async def detect_codex_cli(cli_binary: str = "codex") -> tuple[bool, str | None, str | None]:
    """Detect whether the official OpenAI `codex` CLI binary is available on PATH."""
    binary = shutil.which(cli_binary)
    if not binary:
        return False, None, None
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        version_text = (stdout or stderr).decode("utf-8", errors="replace").strip()
        first_line = next((ln.strip() for ln in version_text.splitlines() if ln.strip()), None)
        return proc.returncode == 0, binary, first_line
    except (OSError, TimeoutError):
        return False, binary, None


def _flatten_messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    """Format single-shot conversation history cleanly for non-interactive stdin."""
    chunks: list[str] = []
    for msg in messages:
        role = str(msg.get("role", "user")).strip().lower()
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            text = "\n".join(p for p in parts if p)
        else:
            text = str(content or "")
        cleaned = text.strip()
        if not cleaned:
            continue
        if role == "system":
            chunks.append(f"[System Instructions]\n{cleaned}")
        elif role == "assistant":
            chunks.append(f"[Previous Assistant]\n{cleaned}")
        else:
            chunks.append(f"[User]\n{cleaned}")
    return "\n\n".join(chunks) if chunks else "Respond concisely."


class OpenAICodexCliTransport:
    """Single-shot non-tool adapter around the official OpenAI Codex CLI (`codex exec`)."""

    name = "openai_codex_cli"

    def __init__(self, *, cli_binary: str = "codex", timeout_seconds: float = 90.0) -> None:
        self.cli_binary = cli_binary
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        if request.tools:
            raise TransportError(
                "OpenAI Subscription (Codex CLI) transport is single-shot non-tool only (A7-2); "
                "tool-calling loops require an official API key."
            )

        binary = shutil.which(self.cli_binary)
        if not binary:
            raise TransportError(
                "OpenAI Codex CLI ('codex') was not found on PATH. "
                "Install it with `npm i -g @openai/codex` and sign in via `codex login`."
            )

        prompt = _flatten_messages_to_prompt(request.messages)
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-m",
                model,
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise TransportError(f"Failed to launch Codex CLI: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise TransportError(
                f"OpenAI Codex CLI timed out after {int(self.timeout_seconds)}s"
            ) from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)
        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            detail = err_text or out_text or f"exit code {proc.returncode}"
            if "login" in detail.lower() or "auth" in detail.lower() or "401" in detail:
                raise TransportError(
                    f"Codex CLI authentication error ({detail[:200]}). "
                    "Run `codex login` to sign in."
                )
            raise TransportError(f"Codex CLI execution failed: {detail[:320]}")

        tokens_in = max(1, len(prompt) // 4)
        tokens_out = max(1, len(out_text) // 4) if out_text else 0
        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=TransportKind.OPENAI_CODEX_CLI,
            billing_mode=provider_cfg.billing_mode or BillingMode.SUBSCRIPTION_CLI,
            content=out_text,
            tool_calls=[],
            tokens_in=tokens_in,
            tokens_cached=0,
            tokens_cache_write=0,
            tokens_out=tokens_out,
            latency_ms=elapsed_ms,
            family=provider_cfg.family or "openai",
        )

    async def health_check(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
    ) -> TransportHealth:
        installed, path, version = await detect_codex_cli(self.cli_binary)
        detail = (
            f"codex CLI ready ({version or path})"
            if installed
            else "codex CLI not found on PATH (install @openai/codex and run codex login)"
        )
        return TransportHealth(
            provider_id=provider_id,
            transport=TransportKind.OPENAI_CODEX_CLI,
            healthy=installed,
            detail=detail,
        )
