"""Credential storage via OS keyring with automatic scrypt + Fernet vault fallback."""

from __future__ import annotations

import base64
import json
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

VAULT_SERVICE_NAME = "foundry-dev"
DEFAULT_VAULT_PATH = Path.home() / "foundry" / "vault" / "credentials.enc"

_ENV_FALLBACK_MAP: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "gemini_free": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class VaultError(Exception):
    """Raised when credential storage or decryption fails."""


def _derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _probe_keyring() -> bool:
    try:
        import keyring
        from keyring.errors import NoKeyringError

        backend = keyring.get_keyring()
        backend_name = type(backend).__name__.lower()
        if "fail" in backend_name or "null" in backend_name:
            return False
        try:
            keyring.get_password(VAULT_SERVICE_NAME, "__foundry_probe__")
        except NoKeyringError:
            return False
        return True
    except Exception:
        return False


class CredentialVault:
    """Stores official API keys cleanly without touching third-party IDE/CLI OAuth files."""

    def __init__(
        self,
        vault_path: Path | None = None,
        passphrase: str | None = None,
        backend_order: Sequence[Literal["keyring", "encrypted_vault"]] = (
            "keyring",
            "encrypted_vault",
        ),
    ) -> None:
        self._vault_path = (vault_path or DEFAULT_VAULT_PATH).expanduser()
        self._passphrase = passphrase or os.environ.get("FOUNDRY_VAULT_PASSPHRASE")
        self._backend_order = tuple(backend_order)
        self._keyring_available: bool | None = None

    @property
    def active_backend(self) -> Literal["keyring", "encrypted_vault"]:
        for backend in self._backend_order:
            if backend == "keyring":
                if self._keyring_available is None:
                    self._keyring_available = _probe_keyring()
                if self._keyring_available:
                    return "keyring"
            elif backend == "encrypted_vault":
                return "encrypted_vault"
        return "encrypted_vault"

    def _require_passphrase(self) -> str:
        if not self._passphrase:
            raise VaultError(
                "Vault passphrase required for encrypted_vault backend "
                "(pass passphrase or set FOUNDRY_VAULT_PASSPHRASE)."
            )
        return self._passphrase

    def _read_encrypted_map(self) -> dict[str, str]:
        if not self._vault_path.exists():
            return {}
        passphrase = self._require_passphrase()
        try:
            envelope = json.loads(self._vault_path.read_text(encoding="utf-8"))
            salt = base64.b64decode(envelope["salt"])
            token = envelope["ciphertext"].encode("utf-8")
            fernet = Fernet(_derive_fernet_key(passphrase, salt))
            plaintext = fernet.decrypt(token).decode("utf-8")
            data = json.loads(plaintext)
        except InvalidToken as exc:
            raise VaultError("Invalid vault passphrase for encrypted credentials") from exc
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise VaultError(f"Corrupted encrypted vault at {self._vault_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise VaultError("Corrupted encrypted vault payload")
        return {str(k): str(v) for k, v in data.items()}

    def _write_encrypted_map(self, mapping: dict[str, str]) -> None:
        passphrase = self._require_passphrase()
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        salt = os.urandom(16)
        fernet = Fernet(_derive_fernet_key(passphrase, salt))
        ciphertext = fernet.encrypt(json.dumps(mapping).encode("utf-8")).decode("utf-8")
        envelope = {
            "version": 1,
            "kdf": "scrypt",
            "salt": base64.b64encode(salt).decode("ascii"),
            "ciphertext": ciphertext,
        }
        payload = json.dumps(envelope, indent=2)
        tmp_path = self._vault_path.with_suffix(".tmp")
        if os.name == "posix":
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(tmp_path, flags, stat.S_IRUSR | stat.S_IWUSR)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
            except BaseException:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise
        else:
            tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, self._vault_path)

    def set_secret(self, name: str, secret: str) -> Literal["keyring", "encrypted_vault"]:
        if not secret.strip():
            raise VaultError("Secret value cannot be empty")
        key_name = name.removeprefix("vault:")
        backend = self.active_backend
        if backend == "keyring":
            import keyring

            keyring.set_password(VAULT_SERVICE_NAME, key_name, secret)
            return "keyring"
        mapping = self._read_encrypted_map()
        mapping[key_name] = secret
        self._write_encrypted_map(mapping)
        return "encrypted_vault"

    def get_secret(self, name: str, *, allow_env_fallback: bool = True) -> str | None:
        key_name = name.removeprefix("vault:")
        if self.active_backend == "keyring":
            import keyring

            val = keyring.get_password(VAULT_SERVICE_NAME, key_name)
            if val:
                return val
        elif self._vault_path.exists() and self._passphrase:
            mapping = self._read_encrypted_map()
            if key_name in mapping:
                return mapping[key_name]

        if allow_env_fallback:
            env_var = _ENV_FALLBACK_MAP.get(key_name) or f"FOUNDRY_KEY_{key_name.upper()}"
            env_val = os.environ.get(env_var)
            if env_val:
                return env_val
        return None

    def delete_secret(self, name: str) -> bool:
        key_name = name.removeprefix("vault:")
        if self.active_backend == "keyring":
            import keyring
            from keyring.errors import PasswordDeleteError

            try:
                keyring.delete_password(VAULT_SERVICE_NAME, key_name)
                return True
            except PasswordDeleteError:
                return False
        if not self._vault_path.exists():
            return False
        mapping = self._read_encrypted_map()
        if key_name not in mapping:
            return False
        del mapping[key_name]
        self._write_encrypted_map(mapping)
        return True

    def list_keys(self) -> list[str]:
        if self.active_backend == "encrypted_vault" and self._vault_path.exists():
            return sorted(self._read_encrypted_map().keys())
        return []
