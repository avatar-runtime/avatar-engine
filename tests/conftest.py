# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Test fixtures: a shared SQLite-backed engine + the demo app registered.

The crash/idempotency slice runs against SQLite here (fast, portable CAS path);
the same engine runs against Postgres in CI for the true ``FOR UPDATE SKIP
LOCKED`` path. Side-effect assertions use the demo's idempotent store, which is
pointed at the same database file.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

# Configure the engine BEFORE importing avatar modules that read the env.
# Honor an externally-supplied AVATAR_DATABASE_URL (CI uses Postgres for the
# true FOR UPDATE SKIP LOCKED path); otherwise default to a temp SQLite file.
if not os.environ.get("AVATAR_DATABASE_URL"):
    _DB_DIR = tempfile.mkdtemp(prefix="avatar-test-")
    _DB_PATH = os.path.join(_DB_DIR, "avatar_test.db")
    os.environ["AVATAR_DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"
os.environ["AVATAR_API_KEY"] = "test-key"
os.environ["AVATAR_DEV_MODE"] = "1"  # tests run as local dev (skip prod guards)
os.environ["AVATAR_APP"] = "avatar.demo"
os.environ["AVATAR_LEASE_SECONDS"] = "30"

from sqlalchemy import delete  # noqa: E402

from avatar import demo  # noqa: E402  (registers the demo agent + tools)
from avatar.config import load_settings  # noqa: E402
from avatar.engine import policy, runtime  # noqa: E402
from avatar.engine.db import create_engine, create_session_factory, init_db  # noqa: E402
from avatar.engine.models import AgentRun, AgentRunStep, Approval  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest_asyncio.fixture(scope="session")
async def _engine():
    eng = create_engine(os.environ["AVATAR_DATABASE_URL"])
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(_engine):
    return create_session_factory(_engine)


@pytest_asyncio.fixture(autouse=True)
async def _clean(_engine):
    """Reset ledger + demo store + crash hook between tests."""
    runtime.set_crash_hook(None)
    runtime._CRASHED_POINTS.clear()
    policy.set_policy(None)
    factory = create_session_factory(_engine)
    async with factory() as db:
        await db.execute(delete(AgentRunStep))
        await db.execute(delete(Approval))
        await db.execute(delete(AgentRun))
        await db.commit()
    demo.reset_store()
    yield
