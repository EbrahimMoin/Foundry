"""Unit tests for v1.4 config schemas, CredentialVault, and SecretScanner."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from foundry.config import (
    DecisionMode,
    TransportKind,
    default_foundry_config,
    default_models_config,
    default_project_config,
    load_foundry_config,
    load_models_config,
    load_project_config,
    save_foundry_config,
    save_models_config,
    save_project_config,
)
from foundry.security import CredentialVault, SecretLeakError, SecretScanner, VaultError


def test_pyproject_excludes_litellm_and_enforces_cooldown() -> None:
    pyproject_path = Path("pyproject.toml")
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    deps = [d.lower() for d in data["project"]["dependencies"]]
    assert all("litellm" not in d for d in deps)
    assert any(d.startswith("openai") for d in deps)
    assert any(d.startswith("google-genai") for d in deps)
    assert any(d.startswith("anthropic") for d in deps)
    assert data["tool"]["uv"]["exclude-newer"] == "7 days"


def test_default_configs_round_trip_and_v14_invariants(tmp_path: Path) -> None:
    gcfg = default_foundry_config()
    mcfg = default_models_config()
    pcfg = default_project_config("taskflow")

    assert gcfg.runtime.max_heavy_exec_slots == 2
    assert gcfg.runtime.max_concurrent_tasks == 4
    assert pcfg.budgets.per_task.implement_usd == pytest.approx(0.60)
    assert pcfg.budgets.per_task.review_usd == pytest.approx(0.15)
    assert pcfg.budgets.per_feature.blackbox_tests_usd == pytest.approx(0.30)
    assert pcfg.budgets.per_run_wall_clock_minutes == 90
    assert pcfg.budgets.per_run_max_llm_calls == 600
    assert pcfg.deploy.target == "local_compose"
    assert set(DecisionMode) == {"auto", "approve", "human"}
    assert set(TransportKind) == {
        "openai_compat",
        "google_genai",
        "anthropic",
        "openai_codex_cli",
    }
    assert mcfg.policies.allow_openai_subscription_cli is False
    assert mcfg.providers["openai_subscription_cli"].supports_tool_calling is False

    f_path = save_foundry_config(gcfg, tmp_path / "foundry.yaml")
    m_path = save_models_config(mcfg, tmp_path / "models.yaml")
    p_path = save_project_config(pcfg, tmp_path / ".foundry" / "project.yaml")

    assert load_foundry_config(f_path) == gcfg
    assert load_models_config(m_path) == mcfg
    assert load_project_config(p_path) == pcfg


def test_encrypted_vault_round_trip_and_wrong_passphrase(tmp_path: Path) -> None:
    vault_file = tmp_path / "vault" / "credentials.enc"
    vault = CredentialVault(
        vault_path=vault_file,
        passphrase="correct-horse-battery",
        backend_order=("encrypted_vault",),
    )
    assert vault.active_backend == "encrypted_vault"
    backend = vault.set_secret("vault:openai", "sk-proj-test1234567890abcdef")
    assert backend == "encrypted_vault"
    assert vault.get_secret("vault:openai") == "sk-proj-test1234567890abcdef"
    assert vault.get_secret("openai") == "sk-proj-test1234567890abcdef"
    assert vault.list_keys() == ["openai"]

    wrong_vault = CredentialVault(
        vault_path=vault_file,
        passphrase="wrong-passphrase",
        backend_order=("encrypted_vault",),
    )
    with pytest.raises(VaultError, match="Invalid vault passphrase"):
        wrong_vault.get_secret("openai", allow_env_fallback=False)


def test_secret_scanner_detects_and_blocks_leaked_keys() -> None:
    scanner = SecretScanner()
    fake_openai = "sk-" + "proj-AbCdEf1234567890GhIjKl"
    fake_anthropic = "sk-" + "ant-api03-1234567890abcdef1234567890"
    fake_google = "AIza" + "SyDaB1234567890cDeFgHiJkLmNoPqRsTuV"
    msgs = [
        {"role": "system", "content": "Clean system prompt"},
        {"role": "user", "content": f"Use keys {fake_openai}, {fake_anthropic}, and {fake_google}"},
    ]
    matches = scanner.scan_messages(msgs)
    assert len(matches) == 3
    assert {m.rule for m in matches} == {"openai_api_key", "anthropic_api_key", "google_api_key"}

    redacted = scanner.redact_text(msgs[1]["content"])
    assert fake_openai not in redacted
    assert fake_anthropic not in redacted
    assert fake_google not in redacted
    assert "<REDACTED:openai_api_key>" in redacted
    assert "<REDACTED:anthropic_api_key>" in redacted

    with pytest.raises(SecretLeakError):
        scanner.assert_clean(msgs)

    # Secrets inside tool schemas are also caught
    leaked_tools = [
        {
            "type": "function",
            "function": {"name": "ping", "description": f"Bearer token {fake_openai}"},
        }
    ]
    with pytest.raises(SecretLeakError):
        scanner.assert_clean([{"role": "user", "content": "clean"}], leaked_tools)
