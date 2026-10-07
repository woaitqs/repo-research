"""Message and state primitives.

Mirrors the subset of `langchain_core.messages` that deepagents relies on, plus the
two state reducers the harness depends on:

- `messages`: append, or replace in place when an id already exists
  (deepagents/_messages_reducer.py::_messages_delta_reducer)
- `files`: dict merge where `None` deletes a key
  (deepagents/middleware/filesystem.py::_file_data_reducer)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Message:
    content: str
    id: str = field(default_factory=_new_id)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def role(self) -> str:
        return type(self).__name__.removesuffix("Message").lower()


@dataclass
class SystemMessage(Message):
    pass


@dataclass
class HumanMessage(Message):
    pass


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:8])


@dataclass
class AIMessage(Message):
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolMessage(Message):
    tool_call_id: str = ""
    name: str = ""
    status: str = "success"


@dataclass
class Command:
    """A tool result that updates state beyond appending one ToolMessage."""

    update: dict[str, Any]


@dataclass
class ReplaceMessages:
    """Sentinel: replace the whole message list (deepagents uses REMOVE_ALL_MESSAGES)."""

    messages: list[Message]


def _reduce_messages(current: list[Message], incoming: Any) -> list[Message]:
    if isinstance(incoming, ReplaceMessages):
        return list(incoming.messages)
    result = list(current)
    index = {m.id: i for i, m in enumerate(result)}
    for msg in incoming:
        if msg.id in index:
            result[index[msg.id]] = msg
        else:
            index[msg.id] = len(result)
            result.append(msg)
    return result


def _reduce_files(current: dict[str, str], incoming: dict[str, str | None]) -> dict[str, str]:
    result = dict(current)
    for path, content in incoming.items():
        if content is None:
            result.pop(path, None)
        else:
            result[path] = content
    return result


def apply_update(state: dict[str, Any], update: dict[str, Any] | None) -> None:
    """Merge a partial state update into `state` in place using per-key reducers."""
    for key, value in (update or {}).items():
        if key == "messages":
            state["messages"] = _reduce_messages(state.get("messages", []), value)
        elif key == "files":
            state["files"] = _reduce_files(state.get("files", {}), value)
        else:
            state[key] = value


def count_tokens(messages: list[Message], system_prompt: str = "") -> int:
    """Approximate token count: chars / 4 (deepagents NUM_CHARS_PER_TOKEN = 4)."""
    chars = len(system_prompt)
    for m in messages:
        chars += len(m.content)
        if isinstance(m, AIMessage):
            chars += sum(len(str(tc.args)) + len(tc.name) for tc in m.tool_calls)
    return chars // 4
