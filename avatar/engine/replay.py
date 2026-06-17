# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic replay and fork-from-step.

Two operations:

* :func:`replay_trace` — a pure read of the ledger that reconstructs the run's
  decision sequence **without re-calling the model or re-running any tool**
  (recorded observations stand in for tool results). Proves the trace is a
  faithful, debuggable record.
* :func:`fork_run` — copies steps ``0..from_seq`` of a run into a brand-new run
  and re-queues it so a worker resumes *forward* from that point. The copied
  prefix is replayed (plans read from the ledger, observations reused), so prior
  tool side effects are **not** repeated; only steps after the fork point are
  freshly executed.

Idempotency keys embed the run id, so on copy they are recomputed for the new
run — that is what lets ``rebuild_state`` short-circuit the copied tool calls
instead of re-dispatching them.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from avatar.engine.idempotency import idempotency_key, intent_key
from avatar.engine.models import AgentRun, AgentRunStep


def replay_trace(run: AgentRun, steps: list[AgentRunStep]) -> dict:
    """Reconstruct and summarize a run from its ledger alone (no side effects)."""
    timeline: list[dict] = []
    plans = tool_calls = observations = 0
    output = run.output
    for s in steps:
        if s.type == "plan":
            plans += 1
        elif s.type == "tool_call":
            tool_calls += 1
        elif s.type == "observation":
            observations += 1
        elif s.type == "final":
            output = s.payload.get("output")
        timeline.append(
            {"seq": s.seq, "type": s.type, "tool_call_id": s.tool_call_id,
             "worker_id": s.worker_id, "attempt": s.attempt}
        )
    return {
        "run_id": run.id,
        "status": run.status,
        "reconstructed_output": output,
        "metrics": {"plans": plans, "tool_calls": tool_calls,
                    "observations": observations, "steps": len(steps)},
        "timeline": timeline,
    }


def _args_by_tool_call(steps: list[AgentRunStep]) -> dict[str, dict]:
    """Map tool_call_id -> (name, arguments) from plan steps, for key recompute."""
    out: dict[str, dict] = {}
    for s in steps:
        if s.type == "plan":
            for c in s.payload.get("tool_calls") or []:
                out[c["id"]] = {"name": c["name"], "arguments": c.get("arguments") or {}}
    return out


async def fork_run(db: AsyncSession, source: AgentRun, from_seq: int) -> AgentRun:
    """Create a new run that reuses ``source``'s trace prefix ``0..from_seq``
    and resumes forward. Returns the new (queued) run."""
    steps = list(
        (
            await db.execute(
                select(AgentRunStep)
                .where(AgentRunStep.run_id == source.id, AgentRunStep.seq <= from_seq)
                .order_by(AgentRunStep.seq)
            )
        ).scalars()
    )
    arg_map = _args_by_tool_call(steps)

    new_run = AgentRun(
        agent_ref=source.agent_ref,
        status="queued",
        input=source.input,
        cursor_seq=steps[-1].seq if steps else 0,
        budget_cap_cents=source.budget_cap_cents,
        budget_used_cents=sum(s.cost_cents for s in steps),
        forked_from=source.id,
        fork_seq=from_seq,
    )
    db.add(new_run)
    await db.flush()  # assign new_run.id

    for s in steps:
        idem = None
        if s.type == "observation" and s.tool_call_id in arg_map:
            info = arg_map[s.tool_call_id]
            idem = idempotency_key(new_run.id, s.tool_call_id, info["name"], info["arguments"])
        elif s.type == "tool_call":
            cid = s.payload.get("id")
            if cid in arg_map:
                info = arg_map[cid]
                idem = intent_key(idempotency_key(new_run.id, cid, info["name"], info["arguments"]))
        db.add(
            AgentRunStep(
                run_id=new_run.id,
                seq=s.seq,
                type=s.type,
                payload=s.payload,
                tool_call_id=s.tool_call_id,
                idempotency_key=idem,
                cost_cents=s.cost_cents,
                worker_id=s.worker_id,
                attempt=s.attempt,
            )
        )
    await db.commit()
    return new_run
