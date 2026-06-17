# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Synchronous policy hook: allow | deny | require_approval.

A single callback evaluated before every tool dispatch. The default is
allow-all. A deployment installs its own hook via :func:`set_policy` (or the
SDK's ``policy=`` argument). ``require_approval`` parks the run in
``approval_wait`` for a human to resolve through the dashboard/API.
"""

from __future__ import annotations

from collections.abc import Callable

from avatar.engine.registry import ToolCall

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"

# (agent_ref, ToolCall) -> decision string
PolicyFn = Callable[[str, ToolCall], str]


def _allow_all(agent_ref: str, call: ToolCall) -> str:
    return ALLOW


_POLICY: PolicyFn = _allow_all


def set_policy(fn: PolicyFn | None) -> None:
    global _POLICY
    _POLICY = fn or _allow_all


def evaluate(agent_ref: str, call: ToolCall) -> str:
    decision = _POLICY(agent_ref, call)
    if decision not in (ALLOW, DENY, REQUIRE_APPROVAL):
        raise ValueError(f"policy hook returned invalid decision: {decision!r}")
    return decision
