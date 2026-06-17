# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""The durable execution core.

Postgres is the only system of record. The :data:`runs` table is the queue;
:data:`run_steps` is an append-only ledger from which all run state is derived.
"""

from __future__ import annotations

from avatar.engine.models import (
    RUN_STATUSES,
    STEP_TYPES,
    AgentRun,
    AgentRunStep,
    Base,
)
from avatar.engine.runtime import execute_run, rebuild_state
from avatar.engine.worker import claim_next_run, heartbeat, tick_once

__all__ = [
    "AgentRun",
    "AgentRunStep",
    "Base",
    "RUN_STATUSES",
    "STEP_TYPES",
    "execute_run",
    "rebuild_state",
    "claim_next_run",
    "heartbeat",
    "tick_once",
]
