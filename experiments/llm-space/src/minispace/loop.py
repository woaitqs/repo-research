"""One model step. Tool execute returns terminate, so the step never runs tools."""

from __future__ import annotations

from typing import Any

from minispace.model import ScriptedModel


RUN_LAST_MESSAGE_ERROR = (
    "The last message must be a user message or a tool call result."
)


def is_runnable(messages: list[dict[str, Any]]) -> bool:
    if not messages:
        return True
    last = messages[-1]
    if last.get("role") == "user":
        return True
    return bool(last.get("tool_calls"))


class PiStep:
    """Stand-in for agentLoopContinue when every tool is step-by-step.

    The real package stops the inner loop when every tool result sets
    terminate. This object never calls an executor. `executed` stays empty.
    """

    def __init__(self, model: ScriptedModel):
        self.model = model
        self.executed: list[str] = []

    def step(self, request: dict[str, Any]) -> dict[str, Any]:
        message = self.model.complete(request)
        if message.get("tool_calls"):
            # TERMINATE_PI: do not run the tools. The workbench owns that.
            message = {**message, "terminated": True}
        else:
            message = {**message, "terminated": False}
        return message
