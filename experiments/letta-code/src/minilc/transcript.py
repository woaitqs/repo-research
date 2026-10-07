"""Append-only conversation transcript plus an in-context id list.

Mirrors letta-code's local store (`src/backend/local/local-store.ts`,
`local-transcript.ts`): every message is appended to `messages.jsonl` as a
`message` entry with `id`/`parentId`; compaction appends a `compaction` entry
and replaces only `in_context_ids`. Nothing already written is rewritten, so
the full history stays on disk (letta calls it recall memory) while the model
sees a bounded view.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

Message = dict[str, Any]  # {"id", "role": user|assistant|toolResult, "content", ...}


@dataclass
class Transcript:
    path: str
    messages: dict[str, Message] = field(default_factory=dict)
    in_context_ids: list[str] = field(default_factory=list)
    _last_entry_id: str | None = None

    def __post_init__(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            self._write({"type": "session", "id": "session", "parentId": None})

    # -- disk ------------------------------------------------------------
    def _write(self, entry: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def rows(self) -> list[dict[str, Any]]:
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    # -- writes ----------------------------------------------------------
    def append(self, role: str, content: Any, **extra: Any) -> Message:
        message: Message = {"id": f"msg-{uuid.uuid4().hex[:12]}", "role": role, "content": content, **extra}
        self.messages[message["id"]] = message
        self.in_context_ids.append(message["id"])
        self._write({"type": "message", "id": message["id"], "parentId": self._last_entry_id, "message": message})
        self._last_entry_id = message["id"]
        return message

    def append_compaction(self, summary_message: Message, first_kept_id: str | None, kept_ids: list[str]) -> None:
        """Record a compaction: one new entry, and a new (smaller) in-context list."""
        self.messages[summary_message["id"]] = summary_message
        self._write({
            "type": "compaction",
            "id": f"cmp-{uuid.uuid4().hex[:8]}",
            "parentId": self._last_entry_id,
            "firstKeptEntryId": first_kept_id,
            "message": summary_message,
        })
        self.in_context_ids = [summary_message["id"], *kept_ids]

    # -- reads -----------------------------------------------------------
    def view(self) -> list[Message]:
        """The model-visible message list (what letta calls in-context messages)."""
        return [self.messages[i] for i in self.in_context_ids]

    def all_messages(self) -> list[Message]:
        return [row["message"] for row in self.rows() if row["type"] == "message"]

    def tool_result_for(self, tool_call_id: str) -> Message | None:
        for message in self.view():
            if message["role"] == "toolResult" and message.get("tool_call_id") == tool_call_id:
                return message
        return None

    def pending_tool_calls(self) -> list[dict[str, Any]]:
        """Tool calls in context that have no result yet."""
        pending = []
        for message in self.view():
            if message["role"] == "assistant":
                for call in message.get("tool_calls", []):
                    if self.tool_result_for(call["id"]) is None:
                        pending.append(call)
        return pending
