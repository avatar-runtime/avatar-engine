# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Lease-based worker — claim, heartbeat, crash-resume.

Postgres *is* the queue. A worker atomically leases the next runnable row
(``queued``, or a ``leased``/``running`` row whose lease has expired = a crashed
owner), executes it, and renews the lease via heartbeats. Single ownership is
guaranteed by ``FOR UPDATE SKIP LOCKED`` on Postgres and a compare-and-swap on
SQLite. Workers are stateless: all run state lives in the ledger, so a fresh
worker resumes a crashed run rather than restarting it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from avatar.config import Settings, load_settings
from avatar.engine.models import AgentRun, utcnow

logger = logging.getLogger(__name__)


class LeaseLostError(Exception):
    """Raised when a worker discovers it no longer owns a run's lease.

    Another worker reclaimed the run (our lease expired while we stalled), so we
    must stop immediately rather than commit any further step or terminal
    status — that double-commit is exactly what would finish a run, or run a
    tool, twice.
    """


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def heartbeat(db: AsyncSession, run: AgentRun, lease_seconds: int) -> None:
    """Renew the lease, but only if this worker still owns it. The guarded
    conditional UPDATE means a worker whose lease was stolen cannot extend it;
    ``rowcount==0`` proves ownership was lost and we abort the run loop."""
    new_expiry = utcnow() + timedelta(seconds=lease_seconds)
    res = await db.execute(
        update(AgentRun)
        .where(AgentRun.id == run.id, AgentRun.lease_owner == run.lease_owner)
        .values(lease_expires_at=new_expiry)
    )
    await db.commit()
    if res.rowcount != 1:
        raise LeaseLostError(run.id)
    run.lease_expires_at = new_expiry


async def claim_next_run(
    db: AsyncSession, wid: str, settings: Settings
) -> AgentRun | None:
    """Atomically lease the next runnable run, or return None.

    Runnable = ``queued`` OR (``leased``/``running`` with an expired lease).
    A run whose ``attempt`` has reached ``max_attempts`` is moved to ``dead``
    (poison-run guard) and skipped.
    """
    now = utcnow()
    lease_until = now + timedelta(seconds=settings.lease_seconds)
    runnable = (AgentRun.status == "queued") | (
        AgentRun.status.in_(("leased", "running"))
        & (AgentRun.lease_expires_at < now)
    )

    bind = db.get_bind()
    dialect = bind.dialect.name if bind is not None else ""

    if dialect == "postgresql":
        for _ in range(settings.max_attempts + 2):
            rid = (
                await db.execute(
                    select(AgentRun.id)
                    .where(runnable)
                    .order_by(AgentRun.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if rid is None:
                await db.rollback()
                return None
            row = (
                await db.execute(select(AgentRun).where(AgentRun.id == rid))
            ).scalar_one()
            if row.attempt >= settings.max_attempts:
                row.status = "dead"
                row.error_class = "infra"
                row.lease_owner = None
                row.lease_expires_at = None
                await db.commit()
                continue
            row.status = "leased"
            row.lease_owner = wid
            row.lease_expires_at = lease_until
            row.attempt += 1
            await db.commit()
            return row
        return None

    # Portable compare-and-swap path (SQLite et al.).
    for _ in range(settings.max_attempts + 2):
        candidate = (
            await db.execute(
                select(AgentRun).where(runnable).order_by(AgentRun.created_at).limit(1)
            )
        ).scalar_one_or_none()
        if candidate is None:
            return None
        if candidate.attempt >= settings.max_attempts:
            await db.execute(
                update(AgentRun)
                .where(AgentRun.id == candidate.id, AgentRun.status == candidate.status)
                .values(status="dead", error_class="infra",
                        lease_owner=None, lease_expires_at=None)
            )
            await db.commit()
            continue
        if candidate.lease_expires_at is None:
            lease_guard = AgentRun.lease_expires_at.is_(None)
        else:
            lease_guard = AgentRun.lease_expires_at == candidate.lease_expires_at
        res = await db.execute(
            update(AgentRun)
            .where(
                AgentRun.id == candidate.id,
                AgentRun.status == candidate.status,
                lease_guard,
            )
            .values(status="leased", lease_owner=wid, lease_expires_at=lease_until,
                    attempt=AgentRun.attempt + 1)
        )
        await db.commit()
        if res.rowcount == 1:
            return (
                await db.execute(select(AgentRun).where(AgentRun.id == candidate.id))
            ).scalar_one()
        # Lost the race — try the next candidate.
    return None


async def tick_once(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, wid: str
) -> bool:
    """Claim and execute at most one run. Returns True if a run was processed.
    The unit the tests drive. Uses fresh sessions to mirror per-task isolation."""
    from avatar.engine.runtime import execute_run  # lazy: breaks import cycle

    async with session_factory() as db:
        run = await claim_next_run(db, wid, settings)
        if run is None:
            return False
        rid = run.id

    async with session_factory() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == rid))
        ).scalar_one()
        logger.info("worker %s executing run %s (attempt %s)", wid, run.id, run.attempt)
        await execute_run(db, settings, run, worker_id=wid)
        return True


async def run_forever() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    from avatar.config import check_startup_safety
    from avatar.engine.db import create_engine, create_session_factory
    from avatar.engine.registry import load_app

    settings = load_settings()
    check_startup_safety(settings)  # refuse to run with an insecure config
    load_app()  # import the developer app so agents/tools register
    engine = create_engine(settings.database_url, settings)
    session_factory = create_session_factory(engine)
    wid = worker_id()
    logger.info("avatar worker %s started (db=%s)", wid, settings.database_url)

    stop = asyncio.Event()

    def _stop(*_):
        logger.info("worker %s stopping…", wid)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except ValueError:
            pass

    poll = settings.poll_interval_ms / 1000.0
    # Bounded modes for the demo/tests: process at most N runs, and/or give up
    # after some idle time. Unset = run forever.
    max_runs = int(os.getenv("AVATAR_WORKER_MAX_RUNS", "0")) or None
    max_idle = float(os.getenv("AVATAR_WORKER_MAX_IDLE_SECONDS", "0")) or None
    processed = 0
    idle_for = 0.0
    try:
        while not stop.is_set():
            try:
                worked = await tick_once(session_factory, settings, wid)
            except Exception:  # a single bad run must not kill the worker
                logger.exception("worker tick failed")
                worked = False
            if worked:
                processed += 1
                idle_for = 0.0
                if max_runs is not None and processed >= max_runs:
                    break
            else:
                idle_for += poll
                if max_idle is not None and idle_for >= max_idle:
                    break
                await asyncio.sleep(poll)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
