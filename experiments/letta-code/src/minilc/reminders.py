"""Volatile context as `<system-reminder>` parts on the *user* message.

Mirrors `src/reminders/engine.ts:513-606`: time, cwd, git status, agent ids and
the permission mode change every turn, so they are prepended to the turn's user
content as separate text parts instead of being written into the system prompt.
That keeps the system prompt byte-stable across steps (prompt-cache friendly),
and the AGENTS.md rule forbids injecting `role: "system"` notifications.
Subagent turns get no shared reminders (catalog has no `subagent` mode).
"""

from __future__ import annotations

import datetime
from typing import Any


def reminder_parts(*, cwd: str, agent_id: str, conversation_id: str, memory_dir: str,
                   permission_mode: str, now: datetime.datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return [
        {"type": "text", "text": "<system-reminder>\nThis is an automated message providing context about the "
                                 f"user's environment.\n- Local time: {now:%Y-%m-%d %H:%M} UTC\n"
                                 f"- Current working directory: {cwd}\n</system-reminder>"},
        {"type": "text", "text": f"<system-reminder>\n- Agent ID: {agent_id}\n- Conversation ID: {conversation_id}\n"
                                 f"- Memory directory: {memory_dir}\n</system-reminder>"},
        {"type": "text", "text": f"<system-reminder>Permission mode active: {permission_mode}.</system-reminder>"},
    ]
