"""Async Postgres engine lifecycle, Alembic migration runner, and EmbeddingProvider."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from foundry.blackboard.dsn import resolve_dsn, to_sqlalchemy_async_dsn, to_sqlalchemy_sync_dsn

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Pluggable local embedding interface for fuzzy-memory retrieval (`fastembed` ONNX)."""

    model_name: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class DeterministicHashEmbedder:
    """Zero-download 384-dim deterministic unit-vector embedder for offline tests."""

    def __init__(
        self,
        model_name: str = "deterministic-hash-384",
        dimensions: int = 384,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vec = [
                ((digest[idx % len(digest)] / 255.0) * 2.0) - 1.0 for idx in range(self.dimensions)
            ]
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([round(v / norm, 6) for v in vec])
        return vectors


class FastEmbedProvider:
    """Lazy-loading local ONNX CPU embedder (`BAAI/bge-small-en-v1.5`, <200 MB RAM)."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        dimensions: int = 384,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self._model: Any | None = None

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            mod = importlib.import_module("fastembed")
            self._model = mod.TextEmbedding(model_name=self.model_name)
        return [list(map(float, vec)) for vec in self._model.embed(texts)]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed_sync, list(texts))


def run_migrations_sync(dsn: str | None = None, revision: str = "head") -> None:
    """Apply Blackboard Alembic migrations synchronously using psycopg v3."""
    sync_url = to_sqlalchemy_sync_dsn(resolve_dsn(dsn))
    cfg = Config(str(_MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, revision)


async def run_migrations(dsn: str | None = None, revision: str = "head") -> None:
    """Apply Blackboard Alembic migrations safely off the event loop."""
    await asyncio.to_thread(run_migrations_sync, dsn, revision)


class BlackboardDB:
    """Owns the SQLAlchemy AsyncEngine and sessionmaker for Foundry's Postgres Blackboard."""

    def __init__(self, dsn: str | None = None, *, echo: bool = False) -> None:
        self.raw_dsn = resolve_dsn(dsn)
        self.async_dsn = to_sqlalchemy_async_dsn(self.raw_dsn)
        self.engine: AsyncEngine = create_async_engine(
            self.async_dsn,
            echo=echo,
            pool_pre_ping=True,
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )

    async def migrate(self, revision: str = "head") -> None:
        await run_migrations(self.raw_dsn, revision=revision)

    async def aclose(self) -> None:
        await self.engine.dispose()
