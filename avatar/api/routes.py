# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Control API endpoints (§ control surface).

All ``/v1`` routes require the single static API key. SSE streams the ledger as
steps commit. The dashboard and SDK are both pure clients of these routes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from avatar.api.app import get_session, require_auth
from avatar.engine.models import (
    RUN_STATUSES,
    TERMINAL_STATUSES,
    AgentRun,
    AgentRunStep,
    Approval,
    utcnow,
)
from avatar.engine.replay import fork_run

router = APIRouter()


async def rate_limit(request: Request) -> None:
    """Throttle the write path so a client cannot flood the queue."""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None and not limiter.allow():
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": "1"},
        )


# --- request/response models -------------------------------------------------


class CreateRun(BaseModel):
    agent_ref: str
    input: dict[str, Any] = Field(default_factory=dict)
    budget_cap_cents: int | None = None
    idempotency_key: str | None = None


class ReplayReq(BaseModel):
    from_seq: int


def _run_summary(r: AgentRun) -> dict:
    return {
        "id": r.id,
        "agent_ref": r.agent_ref,
        "status": r.status,
        "attempt": r.attempt,
        "cursor_seq": r.cursor_seq,
        "budget_cap_cents": r.budget_cap_cents,
        "budget_used_cents": r.budget_used_cents,
        "error_class": r.error_class,
        "output": r.output,
        "forked_from": r.forked_from,
        "fork_seq": r.fork_seq,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _step_dict(s: AgentRunStep) -> dict:
    return {
        "seq": s.seq,
        "type": s.type,
        "payload": s.payload,
        "tool_call_id": s.tool_call_id,
        "idempotency_key": s.idempotency_key,
        "cost_cents": s.cost_cents,
        "worker_id": s.worker_id,
        "attempt": s.attempt,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


async def _get_run(db: AsyncSession, run_id: str) -> AgentRun:
    run = (
        await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


# --- endpoints ---------------------------------------------------------------


@router.post(
    "/v1/runs",
    status_code=202,
    dependencies=[Depends(require_auth), Depends(rate_limit)],
)
async def create_run(
    body: CreateRun, request: Request, db: AsyncSession = Depends(get_session)
) -> dict:
    if body.idempotency_key:
        existing = (
            await db.execute(
                select(AgentRun).where(AgentRun.idempotency_key == body.idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"id": existing.id, "status": existing.status}
    # Backpressure: refuse new work when the queue is already saturated.
    cap = request.app.state.settings.max_queue_depth
    if cap > 0:
        queued = (
            await db.execute(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.status == "queued")
            )
        ).scalar_one()
        if queued >= cap:
            raise HTTPException(
                status_code=429,
                detail=f"queue is full ({queued} queued, cap {cap})",
                headers={"Retry-After": "5"},
            )
    run = AgentRun(
        agent_ref=body.agent_ref,
        input=body.input,
        budget_cap_cents=body.budget_cap_cents,
        idempotency_key=body.idempotency_key,
        status="queued",
    )
    db.add(run)
    await db.commit()
    return {"id": run.id, "status": run.status}


@router.get("/v1/runs", dependencies=[Depends(require_auth)])
async def list_runs(
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
) -> dict:
    q = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(min(limit, 200))
    if status:
        q = q.where(AgentRun.status == status)
    rows = (await db.execute(q)).scalars().all()
    return {"runs": [_run_summary(r) for r in rows]}


@router.get("/v1/runs/{run_id}", dependencies=[Depends(require_auth)])
async def get_run(run_id: str, db: AsyncSession = Depends(get_session)) -> dict:
    return _run_summary(await _get_run(db, run_id))


@router.get("/v1/runs/{run_id}/steps", dependencies=[Depends(require_auth)])
async def get_steps(run_id: str, db: AsyncSession = Depends(get_session)) -> list[dict]:
    await _get_run(db, run_id)
    rows = (
        await db.execute(
            select(AgentRunStep)
            .where(AgentRunStep.run_id == run_id)
            .order_by(AgentRunStep.seq)
        )
    ).scalars().all()
    return [_step_dict(s) for s in rows]


@router.post("/v1/runs/{run_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_session)) -> dict:
    run = await _get_run(db, run_id)
    if run.status in TERMINAL_STATUSES:
        return _run_summary(run)
    run.cancel_requested = True
    # If it never started, cancel immediately.
    if run.status == "queued":
        run.status = "failed"
        run.error_class = "cancelled"
        run.output = {"error": "cancelled before start"}
    await db.commit()
    return _run_summary(run)


@router.post("/v1/runs/{run_id}/approve", dependencies=[Depends(require_auth)])
async def approve_run(run_id: str, db: AsyncSession = Depends(get_session)) -> dict:
    return await _resolve_approval(db, run_id, "approved")


@router.post("/v1/runs/{run_id}/reject", dependencies=[Depends(require_auth)])
async def reject_run(run_id: str, db: AsyncSession = Depends(get_session)) -> dict:
    return await _resolve_approval(db, run_id, "rejected")


async def _resolve_approval(db: AsyncSession, run_id: str, decision: str) -> dict:
    run = await _get_run(db, run_id)
    if run.status != "approval_wait":
        raise HTTPException(status_code=409, detail="run is not awaiting approval")
    appr = (
        await db.execute(
            select(Approval)
            .where(Approval.run_id == run_id, Approval.status == "pending")
            .order_by(Approval.created_at.desc())
        )
    ).scalars().first()
    if appr is None:
        raise HTTPException(status_code=409, detail="no pending approval")
    appr.status = decision
    appr.decided_at = utcnow()
    # Re-queue so a worker resumes and either dispatches or records the rejection.
    run.status = "queued"
    run.lease_owner = None
    run.lease_expires_at = None
    await db.commit()
    return _run_summary(run)


@router.post("/v1/runs/{run_id}/replay", dependencies=[Depends(require_auth)])
async def replay_run(
    run_id: str, body: ReplayReq, db: AsyncSession = Depends(get_session)
) -> dict:
    source = await _get_run(db, run_id)
    new_run = await fork_run(db, source, body.from_seq)
    return {"id": new_run.id, "status": new_run.status,
            "forked_from": run_id, "fork_seq": body.from_seq}


@router.get("/v1/runs/{run_id}/stream", dependencies=[Depends(require_auth)])
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    factory = request.app.state.session_factory

    async def event_gen():
        last_seq = 0
        # Replay existing steps first, then tail new ones.
        while True:
            if await request.is_disconnected():
                return
            async with factory() as db:
                run = (
                    await db.execute(select(AgentRun).where(AgentRun.id == run_id))
                ).scalar_one_or_none()
                if run is None:
                    yield _sse({"event": "error", "detail": "run not found"})
                    return
                steps = (
                    await db.execute(
                        select(AgentRunStep)
                        .where(AgentRunStep.run_id == run_id, AgentRunStep.seq > last_seq)
                        .order_by(AgentRunStep.seq)
                    )
                ).scalars().all()
                for s in steps:
                    last_seq = s.seq
                    yield _sse({"event": "step", **_step_dict(s)})
                if run.status in TERMINAL_STATUSES or run.status == "approval_wait":
                    yield _sse({"event": "status", "status": run.status,
                                "output": run.output})
                    return
            await asyncio.sleep(0.3)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@router.get("/readyz")
async def readyz(request: Request) -> dict:
    factory = request.app.state.session_factory
    try:
        async with factory() as db:
            await db.execute(select(AgentRun.id).limit(1))
        return {"ready": True}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"db not ready: {exc}") from exc


async def _fleet_stats(db: AsyncSession) -> dict:
    """Operational snapshot: runs by status, dead count, oldest-queued age."""
    rows = (
        await db.execute(
            select(AgentRun.status, func.count()).group_by(AgentRun.status)
        )
    ).all()
    by_status = {s: 0 for s in RUN_STATUSES}
    by_status.update({status: n for status, n in rows})
    oldest_queued = (
        await db.execute(
            select(func.min(AgentRun.created_at)).where(AgentRun.status == "queued")
        )
    ).scalar_one_or_none()
    age = (utcnow() - oldest_queued).total_seconds() if oldest_queued else 0.0
    return {
        "by_status": by_status,
        "queue_depth": by_status.get("queued", 0),
        "running": by_status.get("leased", 0) + by_status.get("running", 0),
        "dead": by_status.get("dead", 0),
        "oldest_queued_age_seconds": round(age, 1),
    }


@router.get("/v1/stats", dependencies=[Depends(require_auth)])
async def stats(db: AsyncSession = Depends(get_session)) -> dict:
    return await _fleet_stats(db)


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> str:
    """Prometheus text exposition. Unauthenticated by convention so a scraper
    can reach it; put it behind your network policy / reverse proxy."""
    factory = request.app.state.session_factory
    async with factory() as db:
        s = await _fleet_stats(db)
    lines = [
        "# HELP avatar_runs Total runs by status.",
        "# TYPE avatar_runs gauge",
    ]
    for status, n in s["by_status"].items():
        lines.append(f'avatar_runs{{status="{status}"}} {n}')
    lines += [
        "# HELP avatar_queue_depth Runs currently queued.",
        "# TYPE avatar_queue_depth gauge",
        f"avatar_queue_depth {s['queue_depth']}",
        "# HELP avatar_runs_running Runs currently leased/running.",
        "# TYPE avatar_runs_running gauge",
        f"avatar_runs_running {s['running']}",
        "# HELP avatar_runs_dead Dead-lettered (poison) runs.",
        "# TYPE avatar_runs_dead gauge",
        f"avatar_runs_dead {s['dead']}",
        "# HELP avatar_oldest_queued_age_seconds Age of the oldest queued run.",
        "# TYPE avatar_oldest_queued_age_seconds gauge",
        f"avatar_oldest_queued_age_seconds {s['oldest_queued_age_seconds']}",
    ]
    return "\n".join(lines) + "\n"
