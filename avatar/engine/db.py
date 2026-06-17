# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Async database setup (SQLAlchemy 2.0).

Postgres in production (true ``FOR UPDATE SKIP LOCKED``); SQLite for fast tests.
Selected via ``AVATAR_DATABASE_URL``. ``init_db`` creates the tables for
dev/test; production owns the schema via ``schema.sql`` (see docs/deployment.md).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from avatar.config import Settings
from avatar.engine.models import Base

SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"


def create_engine(database_url: str, settings: Settings | None = None) -> AsyncEngine:
    """Create the async engine.

    For Postgres, apply the connection-pool bounds from ``settings`` so a fleet
    of workers + API replicas cannot exhaust the server's connection slots.
    SQLite ignores pooling (single-file, per-connection).
    """
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        # SQLite needs check_same_thread off for the async driver.
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs.pop("pool_pre_ping", None)
    elif settings is not None:
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=1800,
        )
    return create_async_engine(database_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create the engine tables. Idempotent; safe for dev/test bootstrap.

    Production should apply ``schema.sql`` (the reviewed canonical DDL) via
    :func:`migrate`; a drift test asserts the two stay in sync.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _split_sql(sql: str) -> list[str]:
    """Split a DDL script into executable statements.

    Strips ``--`` comments (whole-line *and* inline) before splitting on ``;`` —
    inline comments can contain semicolons, which would otherwise truncate a
    statement. (Safe here: schema.sql has no ``--`` inside string literals.)
    """
    stripped = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        stripped.append(line)
    return [s.strip() for s in "\n".join(stripped).split(";") if s.strip()]


async def migrate(engine: AsyncEngine) -> bool:
    """Idempotently ensure the schema exists. Returns True if it created it.

    The schema is created from the ORM models (``create_all``) so it is, by
    construction, exactly what the engine expects on every dialect — no parallel
    hand-written DDL to drift. ``schema.sql`` is the reviewed *documentation* of
    that shape; ``tests/test_schema_drift.py`` pins the two together. Versioned
    schema changes (post-v1) are the job of Alembic (see docs/deployment.md).

    Re-running is a no-op once the ``runs`` table exists, so it is safe as a
    compose step.
    """
    async with engine.begin() as conn:
        has_runs = await conn.run_sync(lambda c: "runs" in inspect(c).get_table_names())
    if has_runs:
        return False
    await init_db(engine)
    return True
