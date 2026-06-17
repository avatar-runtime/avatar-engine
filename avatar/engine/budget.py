# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Per-run budget hard-stop.

Each model/tool step attributes a ``cost_cents``; the engine accumulates it on
``runs.budget_used_cents`` and refuses to proceed once the cap would be
breached, failing the run cleanly with ``error_class='budget'``.
"""

from __future__ import annotations

from avatar.engine.models import AgentRun


def would_exceed(run: AgentRun, additional_cents: int) -> bool:
    if run.budget_cap_cents is None:
        return False
    return (run.budget_used_cents + max(0, additional_cents)) > run.budget_cap_cents


def charge(run: AgentRun, cents: int) -> None:
    run.budget_used_cents = (run.budget_used_cents or 0) + max(0, int(cents))
