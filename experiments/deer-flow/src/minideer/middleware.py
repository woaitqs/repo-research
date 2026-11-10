"""Middleware protocol.

DeerFlow's lead chain is a list of LangChain AgentMiddleware. The first entry
is the outermost wrap_model_call / wrap_tool_call. after_model runs in reverse
registration order (see HitlApprovalMiddleware's comment on SafetyFinishReason).
"""

from __future__ import annotations

from typing import Any, Callable

from minideer.messages import Message
from minideer.model import ModelRequest


ToolHandler = Callable[["ToolCall"], Message]


class EndTurn(Exception):
    """Clarification ends the graph (Command goto=END), it does not interrupt()."""

    def __init__(self, message: Message):
        super().__init__(message.content)
        self.message = message


class ToolCall:
    def __init__(self, name: str, args: dict[str, Any], call_id: str, state: dict[str, Any], ctx: Any):
        self.name = name
        self.args = args
        self.id = call_id
        self.state = state
        self.ctx = ctx


class Middleware:
    def before_agent(self, state: dict[str, Any], ctx: Any) -> None:
        return None

    def before_model(self, state: dict[str, Any], ctx: Any) -> None:
        return None

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Message]) -> Message:
        return handler(request)

    def after_model(self, state: dict[str, Any], message: Message, ctx: Any) -> dict[str, Any] | None:
        """Return an optional dict: message, denials, pause."""
        return None

    def wrap_tool_call(self, request: ToolCall, handler: ToolHandler) -> Message:
        return handler(request)

    def after_agent(self, state: dict[str, Any], ctx: Any) -> None:
        return None
