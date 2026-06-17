# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Tool dispatch — governed, timed, idempotency-aware execution.

A tool is a developer function (registered via the SDK ``@tool``) or, in
``subprocess`` isolation mode, a function invoked in a child ``python -m
avatar.engine._tool_runner`` process with a wall-clock timeout and an
output-size cap. Either way the **idempotency key is made available to the
tool** (as the ``Idempotency-Key`` it should forward to any downstream service
and as ``avatar.current_idempotency_key()``), so a re-dispatch in the crash
window is deduped end-to-end iff the tool honors it.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
from typing import Any

from avatar.engine.registry import ToolCall, ToolDef

# Bound to the in-flight tool's idempotency key so tool code can forward it.
_current_idem: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "avatar_idempotency_key", default=None
)


def current_idempotency_key() -> str | None:
    """The idempotency key of the tool call currently executing (or None)."""
    return _current_idem.get()


class ToolError(Exception):
    """A tool raised, timed out, or returned an over-size payload."""

    def __init__(self, message: str, *, error_class: str = "tool"):
        super().__init__(message)
        self.error_class = error_class


async def dispatch_tool(
    td: ToolDef,
    call: ToolCall,
    idem_key: str,
    *,
    timeout: int,
    max_output_bytes: int,
) -> Any:
    """Execute one tool call once. Returns the tool's result (must be
    JSON-serializable). Raises :class:`ToolError` on timeout/oversize/failure;
    the engine records that as an ``error`` observation."""
    eff_timeout = td.timeout or timeout
    use_subprocess = os.getenv("AVATAR_TOOL_ISOLATION", "inproc") == "subprocess" and td.ref

    if use_subprocess:
        result = await _dispatch_subprocess(td, call, idem_key, eff_timeout)
    else:
        result = await _dispatch_inproc(td, call, idem_key, eff_timeout)

    encoded = json.dumps(result, default=str).encode()
    if len(encoded) > max_output_bytes:
        raise ToolError(
            f"tool '{td.name}' output {len(encoded)}B exceeds cap {max_output_bytes}B"
        )
    return result


async def _dispatch_inproc(td: ToolDef, call: ToolCall, idem_key: str, timeout: int) -> Any:
    async def _run() -> Any:
        token = _current_idem.set(idem_key)
        try:
            if asyncio.iscoroutinefunction(td.fn):
                return await td.fn(**call.arguments)
            # Run sync tools off the event loop so a slow tool can be timed out.
            return await asyncio.to_thread(td.fn, **call.arguments)
        finally:
            _current_idem.reset(token)

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except TimeoutError as exc:
        raise ToolError(f"tool '{td.name}' timed out after {timeout}s") from exc
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 — surfaced as a tool error observation
        raise ToolError(f"tool '{td.name}' raised: {exc}") from exc


async def _dispatch_subprocess(td: ToolDef, call: ToolCall, idem_key: str, timeout: int) -> Any:
    payload = json.dumps(
        {
            "app": os.getenv("AVATAR_APP", ""),
            "tool": td.name,
            "arguments": call.arguments,
            "idempotency_key": idem_key,
        }
    )
    proc = await asyncio.create_subprocess_exec(
        os.getenv("PYTHON", "python"),
        "-m",
        "avatar.engine._tool_runner",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(payload.encode()), timeout=timeout
        )
    except TimeoutError as exc:
        proc.kill()
        raise ToolError(f"tool '{td.name}' timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise ToolError(
            f"tool '{td.name}' subprocess failed: {err.decode(errors='replace')[:500]}"
        )
    try:
        envelope = json.loads(out.decode())
    except json.JSONDecodeError as exc:
        raise ToolError(f"tool '{td.name}' returned non-JSON output") from exc
    if not envelope.get("ok"):
        raise ToolError(envelope.get("error", "tool failed"))
    return envelope.get("result")
