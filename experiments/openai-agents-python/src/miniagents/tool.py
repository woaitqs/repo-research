"""Function tools: a JSON-schema'd callable whose failures become observations.

Upstream: `FunctionTool` (`src/agents/tool.py:454`), `@function_tool` (`tool.py:2574-2870`),
`default_tool_error_function` (`tool.py:1980`).

Two upstream rules reproduced here:

* Sync Python functions run in `asyncio.to_thread` so they never block the loop
  (`tool.py:2783-2785`).
* An exception inside a tool (or unparsable arguments) does not crash the run. It is passed
  to `failure_error_function`, and the returned string becomes the tool output. The default
  string is fixed and does not reveal the exception (commit `40956e04`, #5112).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

DEFAULT_TOOL_ERROR = "An error occurred while running the tool. Please try again."
DEFAULT_REJECTION = "Tool execution was not approved."

ToolErrorFunction = Callable[[Any, Exception], str]


def default_tool_error_function(ctx: Any, error: Exception) -> str:
    return DEFAULT_TOOL_ERROR


@dataclass
class FunctionTool:
    name: str
    description: str
    params_json_schema: dict[str, Any]
    on_invoke_tool: Callable[[Any, str], Awaitable[Any]]
    """(RunContext, raw JSON arguments) -> output."""
    needs_approval: bool | Callable[[Any, dict[str, Any]], bool] = False
    failure_error_function: ToolErrorFunction | None = default_tool_error_function
    """None means: re-raise instead of returning an observation."""

    async def invoke(self, ctx: Any, raw_arguments: str) -> str:
        try:
            output = await self.on_invoke_tool(ctx, raw_arguments)
        except Exception as exc:  # Errors are data for the model, unless opted out.
            if self.failure_error_function is None:
                raise
            return self.failure_error_function(ctx, exc)
        return output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)

    def requires_approval(self, ctx: Any, raw_arguments: str) -> bool:
        if callable(self.needs_approval):
            try:
                args = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            return bool(self.needs_approval(ctx, args))
        return bool(self.needs_approval)


_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def _schema_for(func: Callable[..., Any], skip_first: bool) -> dict[str, Any]:
    hints = get_type_hints(func)
    params = list(inspect.signature(func).parameters.values())
    if skip_first:
        params = params[1:]
    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in params:
        properties[p.name] = {"type": _JSON_TYPES.get(hints.get(p.name, str), "string")}
        required.append(p.name)  # Strict schemas list every property as required.
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _takes_context(func: Callable[..., Any]) -> bool:
    params = list(inspect.signature(func).parameters.values())
    return bool(params) and params[0].name in ("ctx", "context", "run_context")


def function_tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    needs_approval: bool | Callable[[Any, dict[str, Any]], bool] = False,
    failure_error_function: ToolErrorFunction | None = default_tool_error_function,
) -> Any:
    def wrap(f: Callable[..., Any]) -> FunctionTool:
        takes_ctx = _takes_context(f)

        async def on_invoke(ctx: Any, raw_arguments: str) -> Any:
            kwargs = json.loads(raw_arguments or "{}")  # Bad JSON -> failure_error_function.
            args = (ctx,) if takes_ctx else ()
            if inspect.iscoroutinefunction(f):
                return await f(*args, **kwargs)
            return await asyncio.to_thread(f, *args, **kwargs)

        return FunctionTool(
            name=name or f.__name__,
            description=description or (inspect.getdoc(f) or "").strip(),
            params_json_schema=_schema_for(f, skip_first=takes_ctx),
            on_invoke_tool=on_invoke,
            needs_approval=needs_approval,
            failure_error_function=failure_error_function,
        )

    return wrap(func) if func is not None else wrap
