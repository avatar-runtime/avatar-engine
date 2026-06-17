# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""The killer demo — a refund that survives a mid-run worker crash exactly once.

Workflow: ``lookup_order → issue_refund → email_customer``. ``issue_refund`` is
the side-effecting tool. A worker is killed *after* it dispatches the refund but
*before* the observation commits (CRASH-C). A fresh worker re-leases the run,
re-dispatches ``issue_refund`` with the **same idempotency key**, and the tool
dedupes on that key. The precise claim: **tool dispatch attempts may repeat, but
tool effects cannot duplicate when idempotency is enforced.**

This module is the developer "app": importing it (``AVATAR_APP=avatar.demo``)
registers the agent and tools. The side-effect store is a separate tiny table so
it survives the crash and is visible to the resuming worker and the assertions.
"""

from __future__ import annotations

import json
import os

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, func, select

from avatar.sdk import Plan, ToolCall, agent, current_idempotency_key, tool

# --- side-effect store (sync, crash-surviving, idempotent) -------------------

_metadata = MetaData()
_side_effects = Table(
    "demo_side_effects",
    _metadata,
    Column("idempotency_key", String(120), primary_key=True),
    Column("tool", String(80)),
    Column("payload", Text),
)
_dispatches = Table(
    "demo_dispatches",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tool", String(80)),
)

_engine = None


def _sync_url() -> str:
    url = os.getenv("AVATAR_DATABASE_URL", "sqlite+aiosqlite:///./avatar.db")
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def _store():
    global _engine
    if _engine is None:
        _engine = create_engine(_sync_url(), future=True)
        _metadata.create_all(_engine)
    return _engine


def _record_side_effect(key: str, tool_name: str, payload: dict) -> bool:
    """Record a side effect keyed by idempotency key. Returns True if newly
    recorded (i.e. this is the first time), False if it was a dedup hit."""
    eng = _store()
    with eng.begin() as conn:
        exists = conn.execute(
            select(_side_effects.c.idempotency_key).where(
                _side_effects.c.idempotency_key == key
            )
        ).first()
        if exists:
            return False
        conn.execute(
            _side_effects.insert().values(
                idempotency_key=key, tool=tool_name, payload=json.dumps(payload)
            )
        )
        return True


def _bump_dispatch(tool_name: str) -> None:
    eng = _store()
    with eng.begin() as conn:
        conn.execute(_dispatches.insert().values(tool=tool_name))


def side_effect_count(tool_name: str = "issue_refund") -> int:
    eng = _store()
    with eng.begin() as conn:
        return conn.execute(
            select(func.count()).select_from(_side_effects).where(
                _side_effects.c.tool == tool_name
            )
        ).scalar_one()


def dispatch_count(tool_name: str = "issue_refund") -> int:
    eng = _store()
    with eng.begin() as conn:
        return conn.execute(
            select(func.count()).select_from(_dispatches).where(
                _dispatches.c.tool == tool_name
            )
        ).scalar_one()


def reset_store() -> None:
    eng = _store()
    with eng.begin() as conn:
        conn.execute(_side_effects.delete())
        conn.execute(_dispatches.delete())


# --- tools -------------------------------------------------------------------


@tool(idempotent=True)
def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "amount_cents": 500, "customer": "ada@example.com"}


@tool(idempotent=True, retries=1)
def issue_refund(order_id: str, cents: int) -> dict:
    """The side-effecting tool. Honors the idempotency key so a re-dispatch in
    the crash window does NOT issue a second refund."""
    key = current_idempotency_key() or f"no-key:{order_id}"
    _bump_dispatch("issue_refund")  # raw dispatch count (every call)
    first_time = _record_side_effect(key, "issue_refund", {"order_id": order_id, "cents": cents})
    return {
        "refunded": True,
        "order_id": order_id,
        "cents": cents,
        "deduped": not first_time,  # True means this dispatch was a no-op
        "idempotency_key": key,
    }


@tool(idempotent=True)
def email_customer(to: str, message: str) -> dict:
    return {"sent": True, "to": to}


# --- agent -------------------------------------------------------------------


@agent("refund-demo")
def refund_bot(state) -> Plan:
    """Deterministic 3-step plan. Stable tool_call ids keep idempotency stable
    across crashes and replays."""
    observed = {m.get("tool_call_id") for m in state.messages if m.get("role") == "tool"}
    order_id = str(state.input.get("order_id", "order-42"))

    if "c1" not in observed:
        return Plan(content="look up the order",
                    tool_calls=[ToolCall(id="c1", name="lookup_order",
                                         arguments={"order_id": order_id})],
                    cost_cents=1)
    if "c2" not in observed:
        return Plan(content="issue the refund",
                    tool_calls=[ToolCall(id="c2", name="issue_refund",
                                         arguments={"order_id": order_id, "cents": 500})],
                    cost_cents=1)
    if "c3" not in observed:
        return Plan(content="email the customer",
                    tool_calls=[ToolCall(id="c3", name="email_customer",
                                         arguments={"to": "ada@example.com",
                                                    "message": "Your refund is processed."})],
                    cost_cents=1)
    return Plan(final=True, output={"status": "refunded", "order_id": order_id}, cost_cents=1)
