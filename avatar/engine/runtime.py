# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""The durable execution loop — ``execute_run``.

Advances one run by committing one step at a time. The invariant that makes it
crash-safe:

    every tool call is preceded by a committed ``tool_call`` (intent) step, and
    its result is recorded under ``UNIQUE(run_id, idempotency_key)``.

So a worker can die at any point; another re-leases the run, rebuilds state from
the ledger (a pure fold), and continues from ``cursor_seq``. No in-memory state
survives a crash, by design. On resume, an already-observed tool call is
short-circuited to its recorded result; a tool dispatched-but-not-observed (the
crash window) is re-dispatched with the *same* idempotency key, so the downstream
dedupes it — at-most-once dispatch from Avatar, exactly-once iff the tool honors
the key.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from avatar.config import Settings
from avatar.engine import budget, policy
from avatar.engine.idempotency import idempotency_key, intent_key
from avatar.engine.models import AgentRun, AgentRunStep, Approval
from avatar.engine.registry import Plan, State, ToolCall, get_agent, get_tool
from avatar.engine.tools import ToolError, dispatch_tool
from avatar.engine.worker import LeaseLostError, heartbeat

logger = logging.getLogger(__name__)


class SimulatedCrash(BaseException):
    """A hard crash injected for the killer demo / tests.

    Subclasses ``BaseException`` (not ``Exception``) so the engine's normal
    error handling never catches it — it propagates out of ``execute_run`` and
    ``tick_once`` exactly as a process death would, leaving the run leased so
    its lease expires and another worker resumes.
    """


# Optional crash hook for tests: set to a callable(point, tool_name).
_CRASH_HOOK = None
_CRASHED_POINTS: set[str] = set()


def set_crash_hook(fn) -> None:
    global _CRASH_HOOK
    _CRASH_HOOK = fn


def _maybe_crash(point: str, tool_name: str) -> None:
    """Crash injection points for the demo. ``AVATAR_CRASH_AFTER_DISPATCH=<tool>``
    kills a real worker process once; a test hook raises ``SimulatedCrash``."""
    if _CRASH_HOOK is not None:
        _CRASH_HOOK(point, tool_name)
    env_tool = os.getenv("AVATAR_CRASH_AFTER_DISPATCH")
    key = f"{point}:{tool_name}"
    if point == "after_dispatch" and env_tool == tool_name and key not in _CRASHED_POINTS:
        _CRASHED_POINTS.add(key)
        logger.warning("CRASH INJECTION: killing worker after dispatch of %s", tool_name)
        os._exit(137)  # hard kill — the resuming worker proves crash-safety


# --- state reconstruction (pure fold over the ledger) ------------------------


async def load_steps(db: AsyncSession, run_id: str) -> list[AgentRunStep]:
    res = await db.execute(
        select(AgentRunStep)
        .where(AgentRunStep.run_id == run_id)
        .order_by(AgentRunStep.seq)
    )
    return list(res.scalars())


def rebuild_state(run: AgentRun, steps: list[AgentRunStep]) -> tuple[State, list[ToolCall]]:
    """Reconstruct the model message history and the *pending* tool calls
    (requested by the last plan but not yet observed) — the resume point.

    Pure: identical input ledger always yields identical state, which is what
    makes replay deterministic.
    """
    messages: list[dict] = [{"role": "user", "content": run.input}]
    observed_keys: set[str] = set()
    last_plan_calls: list[ToolCall] = []

    for s in steps:
        if s.type == "plan":
            calls = [ToolCall.from_dict(c) for c in (s.payload.get("tool_calls") or [])]
            last_plan_calls = calls
            messages.append(
                {
                    "role": "assistant",
                    "content": s.payload.get("content", ""),
                    "tool_calls": [c.to_dict() for c in calls],
                }
            )
        elif s.type == "observation":
            if s.idempotency_key:
                observed_keys.add(s.idempotency_key)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": s.tool_call_id,
                    "content": json.dumps(s.payload.get("result"), default=str),
                }
            )
        elif s.type == "final":
            last_plan_calls = []  # nothing pending after a final

    pending = [
        c
        for c in last_plan_calls
        if idempotency_key(run.id, c.id, c.name, c.arguments) not in observed_keys
    ]
    state = State(run_id=run.id, input=run.input, messages=messages)
    return state, pending


# --- step commit (advances cursor_seq in the same transaction) ---------------


async def commit_step(
    db: AsyncSession,
    run: AgentRun,
    type_: str,
    payload: dict,
    *,
    worker_id: str,
    tool_call_id: str | None = None,
    idem: str | None = None,
    cost_cents: int = 0,
) -> AgentRunStep:
    seq = run.cursor_seq + 1
    step = AgentRunStep(
        run_id=run.id,
        seq=seq,
        type=type_,
        payload=payload,
        tool_call_id=tool_call_id,
        idempotency_key=idem,
        cost_cents=cost_cents,
        worker_id=worker_id,
        attempt=run.attempt,
    )
    db.add(step)
    run.cursor_seq = seq
    await db.commit()
    return step


# --- the loop ----------------------------------------------------------------


async def execute_run(
    db: AsyncSession, settings: Settings, run: AgentRun, *, worker_id: str
) -> None:
    """Drive a leased run to a terminal/waiting state. Resumable and
    idempotent. Never raises for ordinary failures — they are recorded on the
    run — but lets :class:`SimulatedCrash` and :class:`LeaseLostError`
    propagate (a crash / a stolen lease must stop us without touching state)."""
    agent = get_agent(run.agent_ref)
    if agent is None:
        await _fail(db, run, "infra", f"unknown agent_ref: {run.agent_ref}", worker_id)
        return

    run.status = "running"
    await db.commit()

    try:
        while True:
            # Ownership + liveness: renew the lease and confirm we still own it.
            await heartbeat(db, run, settings.lease_seconds)

            await db.refresh(run, ["cancel_requested"])
            if run.cancel_requested:
                await _fail(db, run, "cancelled", "cancelled by request", worker_id)
                return

            steps = await load_steps(db, run.id)
            run.budget_used_cents = sum(s.cost_cents for s in steps)
            state, pending = rebuild_state(run, steps)

            # Runaway guard.
            plan_count = sum(1 for s in steps if s.type == "plan")
            if plan_count >= settings.max_steps:
                await _fail(db, run, "infra", "max steps exceeded", worker_id)
                return

            if pending:
                parked = await _execute_pending(
                    db, settings, run, steps, pending, worker_id
                )
                if parked:
                    return  # approval_wait or terminal
                continue

            # Awaiting a new plan from the model.
            plan = _coerce_plan(agent.model_fn(state))

            if budget.would_exceed(run, plan.cost_cents):
                await _fail(db, run, "budget", "budget cap exceeded (model)", worker_id)
                return

            await commit_step(
                db,
                run,
                "plan",
                {"content": plan.content, "tool_calls": [c.to_dict() for c in plan.tool_calls]},
                worker_id=worker_id,
                cost_cents=plan.cost_cents,
            )

            if plan.is_final:
                await commit_step(
                    db, run, "final", {"output": plan.output}, worker_id=worker_id
                )
                run.output = plan.output if isinstance(plan.output, (dict, list)) else {
                    "result": plan.output
                }
                run.status = "succeeded"
                run.lease_owner = None
                run.lease_expires_at = None
                await db.commit()
                return
            # else: next iteration picks up the pending tool calls.

    except (SimulatedCrash, LeaseLostError):
        raise
    except Exception as exc:  # noqa: BLE001 — engine-level failure is recorded, not raised
        logger.exception("execute_run failed for %s", run.id)
        try:
            await _fail(db, run, "infra", f"engine error: {exc}", worker_id)
        except Exception:  # noqa: BLE001
            pass


async def _execute_pending(
    db: AsyncSession,
    settings: Settings,
    run: AgentRun,
    steps: list[AgentRunStep],
    pending: list[ToolCall],
    worker_id: str,
) -> bool:
    """Execute the tool calls the last plan requested. Returns True if the run
    was parked (approval_wait) or made terminal and the loop must stop."""
    for tc in pending:
        obs_key = idempotency_key(run.id, tc.id, tc.name, tc.arguments)

        # Already observed → short-circuit (covers normal progress + resume).
        if any(s.type == "observation" and s.idempotency_key == obs_key for s in steps):
            continue

        # --- policy hook ---
        decision = policy.evaluate(run.agent_ref, tc)
        if decision == policy.DENY:
            await commit_step(
                db, run, "observation",
                {"tool_call_id": tc.id, "name": tc.name,
                 "result": {"error": "policy_denied"}},
                worker_id=worker_id, tool_call_id=tc.id, idem=obs_key,
            )
            continue
        if decision == policy.REQUIRE_APPROVAL:
            appr = await _approval(db, run.id, tc.id)
            if appr is None:
                db.add(Approval(run_id=run.id, tool_call_id=tc.id, tool_name=tc.name,
                                arguments=tc.arguments, status="pending"))
                await commit_step(
                    db, run, "approval_wait",
                    {"tool_call_id": tc.id, "name": tc.name, "arguments": tc.arguments},
                    worker_id=worker_id, tool_call_id=tc.id,
                )
                run.status = "approval_wait"
                run.lease_owner = None
                run.lease_expires_at = None
                await db.commit()
                return True
            if appr.status == "rejected":
                await commit_step(
                    db, run, "observation",
                    {"tool_call_id": tc.id, "name": tc.name,
                     "result": {"error": "rejected_by_approver"}},
                    worker_id=worker_id, tool_call_id=tc.id, idem=obs_key,
                )
                continue
            # approved → fall through to dispatch

        td = get_tool(tc.name)
        if td is None:
            await commit_step(
                db, run, "observation",
                {"tool_call_id": tc.id, "name": tc.name,
                 "result": {"error": "unknown_tool"}},
                worker_id=worker_id, tool_call_id=tc.id, idem=obs_key,
            )
            continue

        # --- INTENT before dispatch (committed) ---
        ik = intent_key(obs_key)
        if not any(s.idempotency_key == ik for s in steps):
            await commit_step(
                db, run, "tool_call",
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments},
                worker_id=worker_id, tool_call_id=tc.id, idem=ik,
            )

        # --- dispatch (with bounded retries) ---
        attempts = td.retries + 1
        result: Any = None
        last_err: ToolError | None = None
        for _ in range(attempts):
            try:
                result = await dispatch_tool(
                    td, tc, obs_key,
                    timeout=settings.tool_timeout_seconds,
                    max_output_bytes=settings.tool_max_output_bytes,
                )
                last_err = None
                break
            except ToolError as exc:
                last_err = exc

        if last_err is not None:
            await commit_step(
                db, run, "observation",
                {"tool_call_id": tc.id, "name": tc.name,
                 "result": {"error": "tool_error", "detail": str(last_err)}},
                worker_id=worker_id, tool_call_id=tc.id, idem=obs_key,
            )
            continue

        # Crash window: dispatched, side effect may have happened, observation
        # not yet committed. A real worker death here is exactly CRASH-C.
        _maybe_crash("after_dispatch", tc.name)

        await commit_step(
            db, run, "observation",
            {"tool_call_id": tc.id, "name": tc.name, "result": result},
            worker_id=worker_id, tool_call_id=tc.id, idem=obs_key,
            cost_cents=_cost_of(result),
        )
    return False


# --- helpers -----------------------------------------------------------------


def _coerce_plan(value) -> Plan:
    if isinstance(value, Plan):
        return value
    if isinstance(value, dict):
        calls = [
            ToolCall.from_dict(c) if isinstance(c, dict) else c
            for c in (value.get("tool_calls") or [])
        ]
        return Plan(
            content=value.get("content", ""),
            tool_calls=calls,
            final=value.get("final", False),
            output=value.get("output"),
            cost_cents=value.get("cost_cents", 0),
        )
    raise TypeError(f"agent model function must return a Plan, got {type(value)!r}")


def _cost_of(result: Any) -> int:
    if isinstance(result, dict):
        try:
            return int(result.get("_cost_cents", 0))
        except (TypeError, ValueError):
            return 0
    return 0


async def _approval(db: AsyncSession, run_id: str, tool_call_id: str) -> Approval | None:
    res = await db.execute(
        select(Approval).where(
            Approval.run_id == run_id, Approval.tool_call_id == tool_call_id
        )
    )
    return res.scalar_one_or_none()


async def _fail(db: AsyncSession, run: AgentRun, error_class: str, detail: str, worker_id: str) -> None:
    await commit_step(db, run, "error", {"detail": detail, "error_class": error_class},
                      worker_id=worker_id)
    await _terminal(db, run, "failed", error_class, detail, worker_id)


async def _terminal(
    db: AsyncSession, run: AgentRun, status: str, error_class: str, detail: str, worker_id: str
) -> None:
    run.status = status
    run.error_class = error_class
    run.output = {"error": detail}
    run.lease_owner = None
    run.lease_expires_at = None
    await db.commit()
