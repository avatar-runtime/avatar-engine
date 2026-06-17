# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Policy hook (allow/deny/require_approval) and per-run budget hard-stop."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from avatar import demo
from avatar.engine import policy
from avatar.engine.models import AgentRun, AgentRunStep, Approval, utcnow
from avatar.engine.worker import tick_once


async def _enqueue(session_factory, cap=1000) -> str:
    async with session_factory() as db:
        run = AgentRun(agent_ref="refund-demo", input={"order_id": "order-42"},
                       status="queued", budget_cap_cents=cap)
        db.add(run)
        await db.commit()
        return run.id


async def _drain(session_factory, settings, wid="w"):
    for _ in range(30):
        if not await tick_once(session_factory, settings, wid):
            return


@pytest.mark.asyncio
async def test_policy_deny_blocks_side_effect(session_factory, settings):
    policy.set_policy(lambda ref, call: policy.DENY if call.name == "issue_refund" else policy.ALLOW)
    rid = await _enqueue(session_factory)
    await _drain(session_factory, settings)

    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
        steps = (await db.execute(
            select(AgentRunStep).where(AgentRunStep.run_id == rid))).scalars().all()
    # No refund happened; the denial is recorded as an observation.
    assert demo.side_effect_count("issue_refund") == 0
    denied = [s for s in steps if s.type == "observation"
              and s.payload.get("result", {}).get("error") == "policy_denied"]
    assert len(denied) == 1
    assert run.status == "succeeded"  # the agent continued past the denial


@pytest.mark.asyncio
async def test_require_approval_parks_then_resumes(session_factory, settings):
    policy.set_policy(
        lambda ref, call: policy.REQUIRE_APPROVAL if call.name == "issue_refund" else policy.ALLOW
    )
    rid = await _enqueue(session_factory)
    await _drain(session_factory, settings)

    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
    assert run.status == "approval_wait"
    assert demo.side_effect_count("issue_refund") == 0  # not yet executed

    # Approve out-of-band (what POST /approve does) and resume.
    async with session_factory() as db:
        appr = (await db.execute(
            select(Approval).where(Approval.run_id == rid))).scalar_one()
        appr.status = "approved"
        appr.decided_at = utcnow()
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
        run.status = "queued"
        run.lease_owner = None
        run.lease_expires_at = None
        await db.commit()

    await _drain(session_factory, settings)
    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
    assert run.status == "succeeded"
    assert demo.side_effect_count("issue_refund") == 1


@pytest.mark.asyncio
async def test_budget_hardstop(session_factory, settings):
    rid = await _enqueue(session_factory, cap=0)  # cannot afford even the first plan
    await _drain(session_factory, settings)
    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
    assert run.status == "failed"
    assert run.error_class == "budget"
    assert demo.side_effect_count("issue_refund") == 0
