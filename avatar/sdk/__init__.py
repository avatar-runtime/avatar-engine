# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Avatar Python SDK — define agents/tools, enqueue and observe runs.

Two halves, both behind one ``Avatar`` object:

* **Authoring** (runs in the worker): ``@app.agent`` / ``@tool`` register a
  model function and tool functions into the engine registry. The model
  function takes the rebuilt :class:`State` and returns a :class:`Plan` — either
  tool calls to run or a final answer. The engine drives the durable loop; the
  developer never writes crash-handling, ledgers, or idempotency.

* **Control** (runs anywhere): ``app.runs.create/get/list/wait/stream/replay/
  cancel/approve/reject`` are a thin REST/SSE client of the control API.

Example::

    from avatar import Avatar, tool, Plan, ToolCall

    app = Avatar(api_url="http://localhost:8080", api_key="dev-key")

    @tool(timeout=10, retries=2)
    def issue_refund(order_id: str, cents: int) -> dict:
        ...                                  # developer's real side effect

    @app.agent("support-resolver")
    def resolve(state):
        if any(m["role"] == "tool" for m in state.messages):
            return Plan(final=True, output={"status": "refunded"})
        return Plan(tool_calls=[ToolCall(id="c1", name="issue_refund",
                                         arguments={"order_id": "42", "cents": 500})])

    run = app.runs.create(agent_ref="support-resolver", input={"ticket_id": 42})
    print(app.runs.wait(run["id"]))
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from avatar.engine.models import TERMINAL_STATUSES
from avatar.engine.registry import (
    Plan,
    State,
    ToolCall,
    ToolDef,
    register_agent,
    register_tool,
)
from avatar.engine.tools import current_idempotency_key

__all__ = [
    "Avatar",
    "tool",
    "agent",
    "Plan",
    "State",
    "ToolCall",
    "current_idempotency_key",
]


def tool(
    _fn: Callable | None = None,
    *,
    name: str | None = None,
    timeout: int | None = None,
    retries: int = 0,
    idempotent: bool = True,
):
    """Register a developer function as a governed, idempotency-aware tool."""

    def deco(fn: Callable) -> Callable:
        tname = name or fn.__name__
        ref = f"{fn.__module__}:{fn.__qualname__}"
        register_tool(
            ToolDef(name=tname, fn=fn, timeout=timeout, retries=retries,
                    idempotent=idempotent, ref=ref)
        )
        return fn

    return deco(_fn) if callable(_fn) else deco


def agent(ref: str):
    """Register a model function ``(State) -> Plan`` under ``ref``."""

    def deco(fn: Callable[[State], Plan]) -> Callable[[State], Plan]:
        register_agent(ref, fn)
        return fn

    return deco


class _Runs:
    """REST/SSE client namespace for the control API."""

    def __init__(self, client: Avatar):
        self._c = client

    def create(
        self,
        *,
        agent_ref: str,
        input: dict | None = None,
        budget_cap_cents: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"agent_ref": agent_ref, "input": input or {}}
        if budget_cap_cents is not None:
            body["budget_cap_cents"] = budget_cap_cents
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        return self._c._request("POST", "/v1/runs", json=body)

    def get(self, run_id: str) -> dict:
        return self._c._request("GET", f"/v1/runs/{run_id}")

    def list(self, *, status: str | None = None, limit: int = 50) -> dict:
        params = {"limit": limit}
        if status:
            params["status"] = status
        return self._c._request("GET", "/v1/runs", params=params)

    def steps(self, run_id: str) -> list[dict]:
        return self._c._request("GET", f"/v1/runs/{run_id}/steps")

    def cancel(self, run_id: str) -> dict:
        return self._c._request("POST", f"/v1/runs/{run_id}/cancel")

    def approve(self, run_id: str) -> dict:
        return self._c._request("POST", f"/v1/runs/{run_id}/approve")

    def reject(self, run_id: str) -> dict:
        return self._c._request("POST", f"/v1/runs/{run_id}/reject")

    def replay(self, run_id: str, *, from_seq: int) -> dict:
        return self._c._request(
            "POST", f"/v1/runs/{run_id}/replay", json={"from_seq": from_seq}
        )

    def wait(self, run_id: str, *, timeout: float = 60.0, poll: float = 0.3) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            run = self.get(run_id)
            if run.get("status") in TERMINAL_STATUSES or run.get("status") == "approval_wait":
                return run
            time.sleep(poll)
        raise TimeoutError(f"run {run_id} did not finish within {timeout}s")

    def stream(self, run_id: str):
        """Yield step events from the SSE endpoint until the run is terminal."""
        import httpx

        url = f"{self._c.api_url}/v1/runs/{run_id}/stream"
        with httpx.stream("GET", url, headers=self._c._headers(), timeout=None) as r:
            for line in r.iter_lines():
                if line and line.startswith("data: "):
                    yield json.loads(line[len("data: "):])


class Avatar:
    """The SDK entry point: agent/tool authoring + a control-API client."""

    def __init__(self, api_url: str = "http://localhost:8080", api_key: str = "dev-key"):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.runs = _Runs(self)

    # Authoring sugar (mirror the module-level decorators).
    def agent(self, ref: str):
        return agent(ref)

    def tool(self, *args, **kwargs):
        return tool(*args, **kwargs)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _request(self, method: str, path: str, **kwargs) -> Any:
        import httpx

        resp = httpx.request(
            method, f"{self.api_url}{path}", headers=self._headers(), timeout=30.0, **kwargs
        )
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return None
