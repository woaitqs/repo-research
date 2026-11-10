"""A scripted model. It records every request and never talks to a network."""

from __future__ import annotations

from typing import Any


class ScriptedModel:
    def __init__(self, script: list[dict[str, Any]]):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def complete(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        if not self.script:
            return {"role": "assistant", "text": "", "tool_calls": []}
        action = self.script.pop(0)
        return {
            "role": "assistant",
            "text": action.get("text", ""),
            "tool_calls": [
                {
                    "id": call.get("id", f"call_{len(self.requests)}_{index}"),
                    "name": call["name"],
                    "arguments": dict(call.get("arguments") or {}),
                    "output": None,
                    "is_error": False,
                }
                for index, call in enumerate(action.get("tool_calls") or [])
            ],
        }
