# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Subprocess tool runner (isolation mode).

Reads a JSON job ``{app, tool, arguments, idempotency_key}`` on stdin, imports
the developer app so the tool registry is populated, executes the tool with the
idempotency key bound, and writes ``{ok, result}`` / ``{ok: false, error}`` on
stdout. Invoked by :func:`avatar.engine.tools._dispatch_subprocess`.
"""

from __future__ import annotations

import asyncio
import json
import sys

from avatar.engine import registry, tools


async def _amain() -> None:
    job = json.loads(sys.stdin.read() or "{}")
    try:
        registry.load_app(job.get("app") or None)
        td = registry.get_tool(job["tool"])
        if td is None:
            raise RuntimeError(f"unknown tool: {job['tool']}")
        token = tools._current_idem.set(job.get("idempotency_key"))
        try:
            fn = td.fn
            args = job.get("arguments") or {}
            if asyncio.iscoroutinefunction(fn):
                result = await fn(**args)
            else:
                result = fn(**args)
        finally:
            tools._current_idem.reset(token)
        sys.stdout.write(json.dumps({"ok": True, "result": result}, default=str))
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}))


if __name__ == "__main__":
    asyncio.run(_amain())
