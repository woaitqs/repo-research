"""Scripted stand-in for the chat model. Records every request it sees."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from minideer.messages import AIMessage, Message


ScriptStep = AIMessage | Callable[["ModelRequest"], Message]


@dataclass
class ModelRequest:
    system: str
    messages: list[Message]
    state: dict[str, Any]

    def override(self, *, messages: list[Message] | None = None, system: str | None = None) -> "ModelRequest":
        return ModelRequest(
            system=self.system if system is None else system,
            messages=self.messages if messages is None else messages,
            state=self.state,
        )


@dataclass
class ScriptedModel:
    script: list[ScriptStep]
    requests: list[ModelRequest] = field(default_factory=list)

    def invoke(self, request: ModelRequest) -> Message:
        self.requests.append(request)
        if not self.script:
            raise RuntimeError("ScriptedModel ran out of scripted responses")
        step = self.script.pop(0)
        message = step(request) if callable(step) else step
        if message.role != "ai":
            raise TypeError("scripted model steps must return an AIMessage")
        return message
