"""Tool abstraction.

A tool is a named function plus a description the model sees. Tools that need graph
state (e.g. the `task` tool, or file tools backed by state) receive a `ToolRuntime`,
mirroring `langchain.tools.ToolRuntime` injection in upstream deepagents.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from minideep.messages import Command


@dataclass
class ToolRuntime:
    state: dict[str, Any]
    tool_call_id: str


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., str | Command]
    params: dict[str, str] = field(default_factory=dict)

    def run(self, args: dict[str, Any], runtime: ToolRuntime) -> str | Command:
        if "runtime" in inspect.signature(self.func).parameters:
            return self.func(**args, runtime=runtime)
        return self.func(**args)


def tool(func: Callable[..., str | Command]) -> Tool:
    """Decorator: build a `Tool` from a function's name, docstring and parameters."""
    params = {name: str(p.annotation) for name, p in inspect.signature(func).parameters.items() if name != "runtime"}
    return Tool(name=func.__name__, description=(func.__doc__ or "").strip(), func=func, params=params)
