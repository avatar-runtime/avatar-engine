# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""In-process registry of agents and tools.

A worker process imports the developer's "app module" (``AVATAR_APP``,
e.g. ``mypkg.agents`` or ``module:attr``). Importing it runs the ``@agent`` /
``@tool`` decorators, which populate these registries. The engine then looks up
an agent by ``agent_ref`` and tools by name.

This keeps the durability seam server-side: the engine drives the loop and
calls the registered *model function* and *tool functions*; nothing about an
agent's identity has to survive a crash because everything is re-derived from
the ledger + the (re-imported) registry.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# --- plan / tool-call value types --------------------------------------------


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, d: dict) -> ToolCall:
        return cls(
            id=d["id"], name=d["name"], arguments=d.get("arguments") or {}
        )


@dataclass
class Plan:
    """The model's output for one step of the loop.

    Either a list of ``tool_calls`` to execute, or a ``final`` answer. A plan
    with no tool calls is treated as final.
    """

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    final: bool = False
    output: Any = None
    cost_cents: int = 0

    @property
    def is_final(self) -> bool:
        return self.final or not self.tool_calls


@dataclass
class State:
    """Read-only view handed to the model function each iteration. Rebuilt from
    the ledger, so it is identical across crashes/resumes."""

    run_id: str
    input: Any
    messages: list[dict]


# --- tool + agent definitions ------------------------------------------------


@dataclass
class ToolDef:
    name: str
    fn: Callable[..., Any]
    timeout: int | None = None
    retries: int = 0
    idempotent: bool = True
    # Dotted path used to invoke the tool in a subprocess (module:qualname).
    ref: str = ""


# A model function: (State) -> Plan
ModelFn = Callable[[State], Plan]


@dataclass
class AgentDef:
    ref: str
    model_fn: ModelFn


_AGENTS: dict[str, AgentDef] = {}
_TOOLS: dict[str, ToolDef] = {}
_LOADED_APPS: set[str] = set()


def register_agent(ref: str, model_fn: ModelFn) -> None:
    _AGENTS[ref] = AgentDef(ref=ref, model_fn=model_fn)


def register_tool(td: ToolDef) -> None:
    _TOOLS[td.name] = td


def get_agent(ref: str) -> AgentDef | None:
    return _AGENTS.get(ref)


def get_tool(name: str) -> ToolDef | None:
    return _TOOLS.get(name)


def all_tools() -> dict[str, ToolDef]:
    return dict(_TOOLS)


def clear() -> None:
    """Test helper: reset the registries."""
    _AGENTS.clear()
    _TOOLS.clear()
    _LOADED_APPS.clear()


def load_app(spec: str | None = None) -> None:
    """Import the developer app module(s) so decorators register.

    ``spec`` (or ``$AVATAR_APP``) is a comma-separated list of ``module`` or
    ``module:attr`` entries. Importing the module is enough; the ``:attr`` form
    is accepted for symmetry with common conventions.
    """
    spec = spec or os.getenv("AVATAR_APP", "")
    for entry in [s.strip() for s in spec.split(",") if s.strip()]:
        module = entry.split(":", 1)[0]
        if module in _LOADED_APPS:
            continue
        importlib.import_module(module)
        _LOADED_APPS.add(module)
