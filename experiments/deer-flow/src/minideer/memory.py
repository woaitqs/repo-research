"""Passive memory: queue after the run, inject only when the reminder is first written."""

from __future__ import annotations

from typing import Any

from minideer.middleware import Middleware


class MemoryStore:
    def __init__(self) -> None:
        self.facts: dict[tuple[str, str], list[str]] = {}

    def recall(self, user_id: str, agent_name: str) -> str:
        rows = self.facts.get((user_id, agent_name), [])
        return "\n".join(rows)

    def add(self, user_id: str, agent_name: str, fact: str) -> None:
        self.facts.setdefault((user_id, agent_name), [])
        if fact not in self.facts[(user_id, agent_name)]:
            self.facts[(user_id, agent_name)].append(fact)


class MemoryMiddleware(Middleware):
    """Queue user text after the agent finishes. Does not touch the system prompt."""

    def after_agent(self, state: dict[str, Any], ctx: Any) -> None:
        if ctx.memory_mode != "middleware":
            return None
        for message in state["messages"]:
            if message.role == "human" and "remember:" in message.content:
                fact = message.content.split("remember:", 1)[1].strip()
                ctx.memory.add(ctx.user_id, ctx.agent_name, fact)
        return None
