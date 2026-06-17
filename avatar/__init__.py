# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Avatar — a durable execution engine for AI agents.

"Temporal for AI agents." A crash-safe, append-only, replayable state machine
for an LLM agent's ``plan → tool → observe → commit`` loop, backed entirely by
Postgres. A worker can die at any point; another resumes from the ledger and no
tool side effect is dispatched twice from Avatar's side.

The developer-facing surface lives here::

    from avatar import Avatar, agent, tool, Plan, State, ToolCall

See ``avatar.sdk`` for the client and decorators, ``avatar.engine`` for the
durable core, and ``avatar.api`` for the control API.
"""

from __future__ import annotations

__version__ = "0.1.0"

# Re-export the full documented SDK surface so the README/sdk examples
# (``from avatar import Avatar, tool, Plan, ToolCall``) work off the top-level
# package, not just ``avatar.sdk``.
from avatar.sdk import (  # noqa: E402
    Avatar,
    Plan,
    State,
    ToolCall,
    agent,
    current_idempotency_key,
    tool,
)

__all__ = [
    "Avatar",
    "agent",
    "tool",
    "Plan",
    "State",
    "ToolCall",
    "current_idempotency_key",
    "__version__",
]
