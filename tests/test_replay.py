# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Replay / fork-from-step: reuse the trace prefix without re-running tools."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from avatar import demo
from avatar.engine.models import AgentRun, AgentRunStep
from avatar.engine.replay import fork_run, replay_trace
from avatar.engine.worker import tick_once


async def _run_to_completion(session_factory, settings) -> str:
    async with session_factory() as db:
        run = AgentRun(agent_ref="refund-demo", input={"order_id": "order-99"},
                       status="queued", budget_cap_cents=1000)
        db.add(run)
        await db.commit()
        rid = run.id
    for _ in range(30):
        if not await tick_once(session_factory, settings, "w"):
            break
    return rid


@pytest.mark.asyncio
async def test_fork_reuses_prefix_without_rerunning_tool(session_factory, settings):
    rid = await _run_to_completion(session_factory, settings)
    assert demo.side_effect_count("issue_refund") == 1
    dispatches_before = demo.dispatch_count("issue_refund")

    # Fork from just after the refund observation: the refund is in the copied
    # prefix, so it must NOT be re-issued; only email_customer onward re-runs.
    async with session_factory() as db:
        steps = (await db.execute(
            select(AgentRunStep).where(AgentRunStep.run_id == rid)
            .order_by(AgentRunStep.seq))).scalars().all()
        refund_obs_seq = next(
            s.seq for s in steps if s.type == "observation" and s.tool_call_id == "c2"
        )
        source = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
        forked = await fork_run(db, source, refund_obs_seq)
        fid = forked.id

    # Copied prefix is present and attributes are preserved (not re-executed).
    async with session_factory() as db:
        fsteps = (await db.execute(
            select(AgentRunStep).where(AgentRunStep.run_id == fid)
            .order_by(AgentRunStep.seq))).scalars().all()
    assert [s.seq for s in fsteps] == list(range(1, refund_obs_seq + 1))

    # Resume the fork forward.
    for _ in range(30):
        if not await tick_once(session_factory, settings, "w2"):
            break
    async with session_factory() as db:
        forked = (await db.execute(select(AgentRun).where(AgentRun.id == fid))).scalar_one()

    assert forked.status == "succeeded"
    assert forked.forked_from == rid
    # Refund was NOT re-issued and NOT even re-dispatched by the fork.
    assert demo.side_effect_count("issue_refund") == 1
    assert demo.dispatch_count("issue_refund") == dispatches_before


@pytest.mark.asyncio
async def test_replay_trace_is_a_pure_read(session_factory, settings):
    rid = await _run_to_completion(session_factory, settings)
    async with session_factory() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == rid))).scalar_one()
        steps = (await db.execute(
            select(AgentRunStep).where(AgentRunStep.run_id == rid)
            .order_by(AgentRunStep.seq))).scalars().all()
    before = demo.side_effect_count("issue_refund")
    trace = replay_trace(run, steps)
    # Reconstructing the trace runs no tools.
    assert demo.side_effect_count("issue_refund") == before
    assert trace["reconstructed_output"] == {"status": "refunded", "order_id": "order-99"}
    assert trace["metrics"]["observations"] == trace["metrics"]["tool_calls"]
