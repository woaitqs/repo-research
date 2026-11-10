"""Small message types. DeerFlow stores LangChain messages in the thread checkpoint."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _next_id(prefix: str) -> str:
    _next_id.n += 1
    return f"{prefix}{_next_id.n}"


_next_id.n = 0


@dataclass
class Message:
    role: str
    content: str = ""
    id: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _next_id(self.role[:1])


def HumanMessage(content: str, **extra: Any) -> Message:
    return Message("human", content, extra=dict(extra))


def AIMessage(content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> Message:
    return Message("ai", content, tool_calls=list(tool_calls or []))


def ToolMessage(content: str, tool_call_id: str, name: str, *, status: str = "success") -> Message:
    return Message("tool", content, tool_call_id=tool_call_id, name=name, extra={"status": status})


def SystemMessage(content: str, **extra: Any) -> Message:
    return Message("system", content, extra=dict(extra))


def call(name: str, args: dict[str, Any] | None = None, call_id: str | None = None) -> dict[str, Any]:
    return {"id": call_id or _next_id("call"), "name": name, "args": dict(args or {})}
