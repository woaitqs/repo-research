"""Responses-style items: the one data format shared by runner, sessions and model adapters.

Upstream: `src/agents/items.py` (RunItem types, `ItemHelpers`) and
`src/agents/run_internal/items.py` (replay conversion, orphan pruning).

Items are plain dicts, exactly like `TResponseInputItem` upstream:

    {"role": "user", "content": "..."}
    {"type": "message", "role": "assistant", "content": "..."}
    {"type": "function_call", "name": "...", "arguments": "{...}", "call_id": "..."}
    {"type": "function_call_output", "call_id": "...", "output": "..."}

`RunItem` wraps an item with SDK-only metadata (which agent produced it). The metadata is
stripped by `to_input()` before anything is replayed to a model, mirroring
`run_item_to_input_item()` upstream.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

Item = dict[str, Any]


def user_message(text: str) -> Item:
    return {"role": "user", "content": text}


def assistant_message(text: str) -> Item:
    return {"type": "message", "role": "assistant", "content": text}


def function_call(name: str, arguments: dict[str, Any] | str, call_id: str) -> Item:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return {"type": "function_call", "name": name, "arguments": args, "call_id": call_id}


def function_call_output(call_id: str, output: str) -> Item:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def kind(item: Item) -> str:
    """`function_call`, `function_call_output`, `message:user`, `message:assistant`, ..."""
    item_type = item.get("type")
    if item_type in (None, "message"):
        return f"message:{item.get('role')}"
    return str(item_type)


def input_to_list(value: str | list[Item]) -> list[Item]:
    if isinstance(value, str):
        return [user_message(value)]
    return [dict(item) for item in value]


def text_of(item: Item) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # Responses content parts.
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


@dataclass
class RunItem:
    """A generated item plus SDK-only metadata (never sent to the model)."""

    item: Item
    agent_name: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        if self.meta.get("approval"):
            return "tool_approval_item"
        return kind(self.item)

    def to_input(self) -> Item | None:
        # Approval placeholders are observability items, not model input (upstream
        # `run_item_to_input_item` returns None for `tool_approval_item`).
        if self.meta.get("approval"):
            return None
        return dict(self.item)

    def to_json(self) -> dict[str, Any]:
        return {"item": self.item, "agent_name": self.agent_name, "meta": self.meta}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RunItem:
        return cls(item=data["item"], agent_name=data["agent_name"], meta=data.get("meta", {}))


def drop_orphan_function_calls(items: list[Item]) -> list[Item]:
    """Drop function calls that have no output, so a replay never sends a dangling call.

    Upstream: `drop_orphan_function_calls` (`src/agents/run_internal/items.py:211`). Providers
    reject a transcript whose tool call has no matching output.
    """
    answered = {i["call_id"] for i in items if i.get("type") == "function_call_output"}
    return [
        i for i in items if not (i.get("type") == "function_call" and i["call_id"] not in answered)
    ]


def prepare_model_input(caller_input: str | list[Item], generated: list[RunItem]) -> list[Item]:
    """caller input (kept as-is) + replayable generated items (orphans pruned).

    Upstream: `_prepare_turn_input_items` -> `prepare_model_input_items`
    (`src/agents/run_internal/run_loop.py:347`, `src/agents/run_internal/items.py:371`).
    """
    replay = [i for i in (r.to_input() for r in generated) if i is not None]
    return input_to_list(caller_input) + drop_orphan_function_calls(replay)
