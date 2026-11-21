"""Crash-safe LogicalDBManager for isolated per-sandbox Postgres template clones."""

from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote_plus, urlparse, urlunparse

import asyncpg
from pydantic import BaseModel

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{1,50}$")
_LOCK_KEY_LITERAL = "foundry:app_template"


class LogicalDBError(Exception):
    """Raised on invalid identifiers or Postgres logical DB provisioning errors."""


class SandboxDatabase(BaseModel):
    """Connection info for a sandbox-isolated logical database."""

    task_id: str
    db_name: str
    role_name: str
    database_url: str


def validate_sql_identifier(name: str) -> str:
    """Strictly validate PostgreSQL unquoted identifier format to prevent DDL injection."""
    if not _IDENT_RE.match(name):
        raise LogicalDBError(f"Unsafe or invalid PostgreSQL identifier: {name!r}")
    return name


def sanitize_task_db_name(task_id: str) -> tuple[str, str]:
    """Return validated `(sbx_<clean>, role_sbx_<clean>)` SQL identifiers."""
    clean = re.sub(r"[^a-z0-9_]", "_", task_id.lower()).strip("_")
    if not clean:
        raise LogicalDBError(f"Cannot derive DB identifier from task_id: {task_id!r}")
    suffix = clean[:40]
    db_name = validate_sql_identifier(f"sbx_{suffix}")
    role_name = validate_sql_identifier(f"role_sbx_{suffix}")
    return db_name, role_name


def _build_dsn_with_db(base_dsn: str, db_name: str, *, user: str, password: str) -> str:
    parsed = urlparse(base_dsn)
    scheme = parsed.scheme or "postgresql+asyncpg"
    host = parsed.hostname or "foundry-postgres"
    port = parsed.port or 5432
    netloc = f"{quote_plus(user)}:{quote_plus(password)}@{host}:{port}"
    return urlunparse((scheme, netloc, f"/{db_name}", "", "", ""))


class LogicalDBManager:
    """Clones per-task sandbox DBs from `app_template` with advisory locks and session cleanup."""

    def __init__(
        self,
        admin_dsn: str,
        *,
        host_for_sandbox: str = "foundry-postgres",
        port_for_sandbox: int = 5432,
        template_db: str = "app_template",
        connect_fn: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._admin_dsn = admin_dsn
        self._host_for_sandbox = host_for_sandbox
        self._port_for_sandbox = port_for_sandbox
        self._template_db = validate_sql_identifier(template_db)
        self._connect_fn = connect_fn or asyncpg.connect

    async def _terminate_connections(self, conn: Any, db_name: str) -> None:
        validate_sql_identifier(db_name)
        await conn.execute(
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            db_name,
        )

    async def _acquire_template_lock(self, conn: Any) -> None:
        await conn.execute("SELECT pg_advisory_lock(hashtext($1))", _LOCK_KEY_LITERAL)

    async def _release_template_lock(self, conn: Any) -> None:
        await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", _LOCK_KEY_LITERAL)

    async def startup_cleanup(self) -> None:
        """Terminate stale template sessions and re-lock app_template after a crash."""
        conn = await self._connect_fn(self._admin_dsn)
        try:
            await self._acquire_template_lock(conn)
            try:
                exists = await conn.fetchval(
                    "SELECT 1 FROM pg_database WHERE datname = $1",
                    self._template_db,
                )
                if exists:
                    await self._terminate_connections(conn, self._template_db)
                    await conn.execute(
                        f"ALTER DATABASE {self._template_db} WITH ALLOW_CONNECTIONS false"
                    )
            finally:
                await self._release_template_lock(conn)
        finally:
            await conn.close()

    async def create_sandbox_db(self, task_id: str) -> SandboxDatabase:
        """Clone `app_template` into an isolated DB owned by an unprivileged task role."""
        db_name, role_name = sanitize_task_db_name(task_id)
        password = secrets.token_urlsafe(24)

        conn = await self._connect_fn(self._admin_dsn)
        try:
            await self._acquire_template_lock(conn)
            try:
                await self._terminate_connections(conn, self._template_db)
                await conn.execute(f"DROP DATABASE IF EXISTS {db_name}")
                await conn.execute(f"DROP ROLE IF EXISTS {role_name}")
                # Use parameterized quoted literal style isn't accepted for PASSWORD in CREATE ROLE,
                # and password is strictly URL-safe [A-Za-z0-9_-], so single-quoting is safe.
                await conn.execute(
                    f"CREATE ROLE {role_name} LOGIN PASSWORD '{password}' "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                )
                await conn.execute(
                    f"CREATE DATABASE {db_name} TEMPLATE {self._template_db} OWNER {role_name}"
                )
                await conn.execute(f"REVOKE CONNECT ON DATABASE {db_name} FROM PUBLIC")
                await conn.execute(f"GRANT CONNECT ON DATABASE {db_name} TO {role_name}")
            finally:
                await self._release_template_lock(conn)
        finally:
            await conn.close()

        dsn_template = f"postgresql+asyncpg://x:x@{self._host_for_sandbox}:{self._port_for_sandbox}"
        database_url = _build_dsn_with_db(dsn_template, db_name, user=role_name, password=password)
        return SandboxDatabase(
            task_id=task_id,
            db_name=db_name,
            role_name=role_name,
            database_url=database_url,
        )

    async def drop_sandbox_db(self, task_id: str) -> None:
        """Drop sandbox database and isolated role cleanly."""
        db_name, role_name = sanitize_task_db_name(task_id)
        conn = await self._connect_fn(self._admin_dsn)
        try:
            await self._terminate_connections(conn, db_name)
            await conn.execute(f"DROP DATABASE IF EXISTS {db_name}")
            await conn.execute(f"DROP ROLE IF EXISTS {role_name}")
        finally:
            await conn.close()

    async def refresh_app_template(
        self,
        apply_migrations: Callable[[str], Awaitable[None]],
    ) -> None:
        """Safely unlock, migrate, and re-lock app_template under advisory lock."""
        conn = await self._connect_fn(self._admin_dsn)
        try:
            await self._acquire_template_lock(conn)
            try:
                await self._terminate_connections(conn, self._template_db)
                await conn.execute(
                    f"ALTER DATABASE {self._template_db} WITH ALLOW_CONNECTIONS true"
                )
                parsed = urlparse(self._admin_dsn)
                template_dsn = urlunparse(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        f"/{self._template_db}",
                        parsed.params,
                        parsed.query,
                        parsed.fragment,
                    )
                )
                try:
                    await apply_migrations(template_dsn)
                finally:
                    await self._terminate_connections(conn, self._template_db)
                    await conn.execute(
                        f"ALTER DATABASE {self._template_db} WITH ALLOW_CONNECTIONS false"
                    )
            finally:
                await self._release_template_lock(conn)
        finally:
            await conn.close()
