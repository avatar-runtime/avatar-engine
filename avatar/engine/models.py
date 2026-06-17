# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Canonical data model — the two tables that *are* Avatar.

``runs`` is the durable run record and the work queue (workers lease rows).
``run_steps`` is the append-only ledger: seq-ordered, never updated, never
deleted. All run state is a pure fold over its steps (see
:func:`avatar.engine.runtime.rebuild_state`).

The SQLAlchemy types are chosen to run identically on Postgres (production,
``FOR UPDATE SKIP LOCKED``) and SQLite (fast tests). UUIDs and timestamps are
generated Python-side so behaviour is portable. The canonical Postgres DDL is
in ``schema.sql`` — these models must stay in sync with it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

# --- enums (kept as plain strings for cross-dialect portability) -------------

RUN_STATUSES = (
    "queued",
    "leased",
    "running",
    "paused",
    "approval_wait",
    "succeeded",
    "failed",
    "dead",
)
STEP_TYPES = (
    "plan",
    "tool_call",
    "observation",
    "approval_wait",
    "final",
    "error",
)

# Terminal run states: no further work is ever scheduled.
TERMINAL_STATUSES = ("succeeded", "failed", "dead")


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class AgentRun(Base):
    """A durable agent execution and the unit of work in the queue.

    Workers lease rows whose ``status`` is ``queued`` (or whose lease has
    expired — crash recovery). ``cursor_seq`` is the last committed step's seq;
    on resume the engine rebuilds from steps and continues, so no in-memory
    state needs to survive a crash.
    """

    __tablename__ = "runs"
    __table_args__ = (
        # Dispatch hot path: find the next leasable run quickly.
        Index("runs_dispatch_idx", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_ref: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Last committed step seq. Advanced in the same transaction as the insert.
    cursor_seq: Mapped[int] = mapped_column(Integer, default=0)
    # Lease for single-owner, at-least-once distributed execution.
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    # Budget hard-stop (cents). NULL = unlimited.
    budget_cap_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_used_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Failure taxonomy: model|tool|policy|budget|infra|cancelled
    error_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Cooperative cancellation: set by the API, honored by the worker each step.
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    # Caller-supplied idempotency on enqueue (dedups POST /v1/runs).
    idempotency_key: Mapped[str | None] = mapped_column(
        String(200), nullable=True, unique=True
    )
    # Replay/fork provenance (NULL for a fresh run).
    forked_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fork_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AgentRunStep(Base):
    """One immutable step in a run's execution trace — the replayable ledger.

    Steps are append-only: never ``UPDATE`` a payload, never ``DELETE`` a row.
    Two unique constraints encode the engine's guarantees:

    * ``(run_id, seq)`` — a strict, gap-free ordering.
    * ``(run_id, idempotency_key)`` — the exactly-once-record guarantee: it is
      impossible to commit two ``observation`` rows for the same tool call.
    """

    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_runstep_seq"),
        UniqueConstraint("run_id", "idempotency_key", name="uq_runstep_idem"),
        Index("ix_runstep_run", "run_id", "seq"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # Stable id of the model's tool call (drives idempotency derivation).
    tool_call_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Derived idempotency key (see avatar.engine.idempotency).
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Cost attributed to this step, for the per-run budget.
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Worker that committed this step — used to render "resumed after crash".
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    """Human-in-the-loop decision for a ``require_approval`` tool call.

    The ledger remains the source of truth for *execution* state; this side
    table only records the out-of-band human decision the engine reads when it
    re-leases a parked run. One row per (run, tool_call).
    """

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_approval_call"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(80))
    tool_name: Mapped[str] = mapped_column(String(200))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
