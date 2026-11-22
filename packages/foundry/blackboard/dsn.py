"""Postgres DSN resolution and driver scheme normalization helpers."""

from __future__ import annotations

import os

DEFAULT_BLACKBOARD_DSN = "postgresql://foundry:foundry_local_dev@127.0.0.1:5433/foundry_blackboard"


def resolve_dsn(dsn: str | None = None) -> str:
    """Resolve Postgres DSN from explicit argument, FOUNDRY_DATABASE_URL, or local default."""
    return dsn or os.environ.get("FOUNDRY_DATABASE_URL") or DEFAULT_BLACKBOARD_DSN


def _normalize_base_scheme(dsn: str) -> str:
    if dsn.startswith("postgres://"):
        return "postgresql://" + dsn[len("postgres://") :]
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


def to_sqlalchemy_async_dsn(dsn: str) -> str:
    """Return a SQLAlchemy asyncpg DSN (`postgresql+asyncpg://...`)."""
    base = _normalize_base_scheme(dsn)
    if base.startswith("postgresql://"):
        return "postgresql+asyncpg://" + base[len("postgresql://") :]
    return base


def to_sqlalchemy_sync_dsn(dsn: str) -> str:
    """Return a SQLAlchemy psycopg v3 sync DSN (`postgresql+psycopg://...`)."""
    base = _normalize_base_scheme(dsn)
    if base.startswith("postgresql://"):
        return "postgresql+psycopg://" + base[len("postgresql://") :]
    return base


def to_psycopg_dsn(dsn: str) -> str:
    """Return a plain `postgresql://...` DSN suitable for psycopg & asyncpg raw connections."""
    return _normalize_base_scheme(dsn)


to_asyncpg_dsn = to_psycopg_dsn
