# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Nominal (control) execution: a healthy run completes with a clean ledger."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from avatar import demo
from avatar.engine.models import AgentRun, AgentRunStep
from avatar.engine.worker import tick_once


async def _enqueue(session_factory) -> str:
    async with session_factory() as db:
        run = AgentRun(agent_ref="refund-demo", input={"order_id": "order-42"},
                       status="queued", budget_cap_cents=1000)
        db.add(run)
        await db.commit()
        return run.id


async def _drain(session_factory, settings, wid="w1", limit=30):
    for _ in range(limit):
        if not await tick_once(session_factory, settings, wid):
            return


@pytest.mark.asyncio
async def test_nominal_run_completes(session_factory, settings):
    rid = await _enqueue(session_factory)
    await _drain(session_factory, settings)

    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
        steps = (await db.execute(
            select(AgentRunStep).where(AgentRunStep.run_id == rid)
            .order_by(AgentRunStep.seq))).scalars().all()

    assert run.status == "succeeded"
    assert run.output == {"status": "refunded", "order_id": "order-42"}
    # Exactly one refund side effect, and it was charged to the budget.
    assert demo.side_effect_count("issue_refund") == 1
    assert run.budget_used_cents > 0

    # Ledger is gap-free, seq-ordered, and ends with `final`.
    assert [s.seq for s in steps] == list(range(1, len(steps) + 1))
    types = [s.type for s in steps]
    assert types[-1] == "final"
    assert "plan" in types and "tool_call" in types and "observation" in types
    # Every tool_call has a matching observation.
    assert types.count("tool_call") == types.count("observation")


@pytest.mark.asyncio
async def test_unknown_agent_fails_cleanly(session_factory, settings):
    async with session_factory() as db:
        run = AgentRun(agent_ref="does-not-exist", input={}, status="queued")
        db.add(run)
        await db.commit()
        rid = run.id
    await _drain(session_factory, settings)
    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
    assert run.status == "failed"
    assert run.error_class == "infra"
