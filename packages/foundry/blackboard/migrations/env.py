"""Alembic environment for Foundry Blackboard Postgres migrations."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from foundry.blackboard.dsn import resolve_dsn, to_sqlalchemy_sync_dsn
from foundry.blackboard.models import BlackboardBase

config = context.config
target_metadata = BlackboardBase.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or to_sqlalchemy_sync_dsn(resolve_dsn())
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    if not section.get("sqlalchemy.url"):
        section["sqlalchemy.url"] = to_sqlalchemy_sync_dsn(resolve_dsn())
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
