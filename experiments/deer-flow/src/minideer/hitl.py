"""Two different human pauses.

Approval uses after_model and a serializable pause (upstream interrupt()).
DeerFlow's own comment says wrap_tool_call is the wrong place: that path is
bypassed by their execution chain.

Clarification is not an interrupt. The middleware returns the equivalent of
Command(goto=END). A non-interactive run is told to proceed and the loop continues.
"""

from __future__ import annotations

from typing import Any

from minideer.messages import Message, ToolMessage
from minideer.middleware import EndTurn, Middleware, ToolCall, ToolHandler


class HitlApprovalMiddleware(Middleware):
    def __init__(self, tool_names: set[str], *, supports_resume: bool = True):
        self.tool_names = set(tool_names)
        self.supports_resume = supports_resume

    def after_model(self, state: dict[str, Any], message: Message, ctx: Any) -> dict[str, Any] | None:
        targets = [item for item in message.tool_calls if item["name"] in self.tool_names]
        if not targets:
            return None
        if not self.supports_resume or not ctx.interactive:
            return self._deny(message, targets)
        return {
            "pause": {
                "kind": "hitl_approval",
                "calls": list(message.tool_calls),
            }
        }

    def _deny(self, message: Message, targets: list[dict[str, Any]]) -> dict[str, Any]:
        dropped = {item["id"] for item in targets}
        message.tool_calls = [item for item in message.tool_calls if item["id"] not in dropped]
        denials = [
            ToolMessage(
                "denied: non-interactive run cannot pause for approval",
                tool_call_id=item["id"],
                name=item["name"],
                status="error",
            )
            for item in targets
        ]
        return {"message": message, "denials": denials}


class ClarificationMiddleware(Middleware):
    def wrap_tool_call(self, request: ToolCall, handler: ToolHandler) -> Message:
        if request.name != "ask_clarification":
            return handler(request)
        if not request.ctx.interactive:
            return ToolMessage(
                "clarification disabled; proceed without asking",
                tool_call_id=request.id,
                name=request.name,
            )
        message = ToolMessage(
            f"clarification requested: {request.args.get('question', '')}",
            tool_call_id=request.id,
            name=request.name,
        )
        raise EndTurn(message)  # CLARIFICATION_ENDS
