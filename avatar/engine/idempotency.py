# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Idempotency key derivation — the heart of the exactly-once guarantee.

The key is **crash-stable**: a worker that dies between committing a tool_call
intent and its observation must recompute the *same* key on resume, so the
``UNIQUE(run_id, idempotency_key)`` index actually prevents a second record and
the downstream ``Idempotency-Key`` header is identical across attempts.

Honest claim (write it exactly this way in docs):
    at-most-once dispatch from Avatar always; exactly-once end-to-end iff the
    tool honors the key. Never claim unconditional exactly-once.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def idempotency_key(run_id: str, tool_call_id: str, name: str, args: dict[str, Any]) -> str:
    """Stable key for a tool call.

    Keyed on the model-assigned ``tool_call_id`` (persisted in the plan step),
    never on a mutating counter — that is what makes it survive a crash. The
    name + canonical args are folded in so a malformed/duplicate id still maps
    to a distinct effect when the payload differs.
    """
    raw = f"{run_id}:{tool_call_id}:{name}:" + json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def intent_key(observation_key: str) -> str:
    """Idempotency key for the *intent* (tool_call) step.

    The observation owns the canonical key (the exactly-once record); the intent
    gets a distinct ``:call`` variant so both can coexist under the unique index
    while still being deduped independently across resumes.
    """
    return observation_key + ":call"
