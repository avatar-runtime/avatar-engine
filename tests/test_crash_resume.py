# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""The decisive durability slice — CRASH-C.

Kill the worker *after* ``issue_refund`` dispatches but *before* its observation
commits. A fresh worker re-leases the run, rebuilds from the ledger, and the
refund happens **exactly once**. This is the product.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from avatar import demo
from avatar.engine import runtime
from avatar.engine.models import AgentRun, AgentRunStep, utcnow
from avatar.engine.worker import tick_once


async def _enqueue(session_factory) -> str:
    async with session_factory() as db:
        run = AgentRun(agent_ref="refund-demo", input={"order_id": "order-42"},
                       status="queued", budget_cap_cents=1000)
        db.add(run)
        await db.commit()
        return run.id


@pytest.mark.asyncio
async def test_crash_mid_refund_resumes_exactly_once(session_factory, settings):
    rid = await _enqueue(session_factory)

    # Arm a one-shot crash right after the refund dispatch.
    def hook(point, tool):
        if point == "after_dispatch" and tool == "issue_refund":
            runtime.set_crash_hook(None)  # one-shot
            raise runtime.SimulatedCrash()

    runtime.set_crash_hook(hook)

    # Worker A executes and "crashes" mid-refund.
    with pytest.raises(runtime.SimulatedCrash):
        await tick_once(session_factory, settings, "worker-A")

    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
        steps = (await db.execute(
            select(AgentRunStep).where(AgentRunStep.run_id == rid)
            .order_by(AgentRunStep.seq))).scalars().all()
    # Still owned by A (will expire), not terminal. The refund side effect has
    # already happened once; the intent is on the ledger but no observation yet.
    assert run.status == "running"
    assert demo.side_effect_count("issue_refund") == 1
    assert any(s.type == "tool_call" and s.tool_call_id == "c2" for s in steps)
    assert not any(s.type == "observation" and s.tool_call_id == "c2" for s in steps)

    # Simulate the lease expiring (a real worker death lets the 30s lease lapse).
    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    # Worker B re-leases and resumes to completion.
    for _ in range(30):
        if not await tick_once(session_factory, settings, "worker-B"):
            break

    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
        steps = (await db.execute(
            select(AgentRunStep).where(AgentRunStep.run_id == rid)
            .order_by(AgentRunStep.seq))).scalars().all()

    # The whole point:
    assert run.status == "succeeded"
    assert demo.side_effect_count("issue_refund") == 1      # exactly once
    assert demo.dispatch_count("issue_refund") >= 2          # re-dispatched on resume
    # The resume is visible in the ledger (attempt advanced; B committed the obs).
    assert run.attempt >= 2
    obs_c2 = [s for s in steps if s.type == "observation" and s.tool_call_id == "c2"]
    assert len(obs_c2) == 1
    assert obs_c2[0].attempt >= 2
    assert obs_c2[0].payload["result"]["deduped"] is True   # downstream deduped


@pytest.mark.asyncio
async def test_no_duplicate_observation_under_unique_index(session_factory, settings):
    """The UNIQUE(run_id, idempotency_key) index makes a double-record impossible."""
    rid = await _enqueue(session_factory)
    for _ in range(30):
        if not await tick_once(session_factory, settings, "w"):
            break
    async with session_factory() as db:
        steps = (await db.execute(
            select(AgentRunStep).where(AgentRunStep.run_id == rid))).scalars().all()
    obs = [s for s in steps if s.type == "observation"]
    keys = [s.idempotency_key for s in obs]
    assert len(keys) == len(set(keys))  # no duplicate observation keys
