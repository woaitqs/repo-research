"""Middleware contract — the extension point the whole harness is built on.

Mirrors `langchain.agents.middleware.types.AgentMiddleware` as used by deepagents:

- `before_agent(state)`        runs once per invocation, may return a state update
- `wrap_model_call(req, next)` intercepts every model request (onion; first = outermost)
- `wrap_tool_call(req, next)`  intercepts every tool execution
- `tools`                      tools this middleware contributes

The key property: `wrap_model_call` edits a *per-call* `ModelRequest` (system prompt,
messages, tools) without mutating stored state. State changes must be returned
explicitly via `ModelResponse.state_update` (upstream: `ExtendedModelResponse.command`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from minideep.messages import AIMessage, Command, ToolCall, ToolMessage

if TYPE_CHECKING:
    from minideep.model import ChatModel
    from minideep.messages import Message
    from minideep.tools import Tool


@dataclass
class ModelRequest:
    model: ChatModel
    system_prompt: str
    messages: list[Message]
    tools: list[Tool]
    state: dict[str, Any]

    def override(self, **changes: Any) -> ModelRequest:
        return replace(self, **changes)


@dataclass
class ModelResponse:
    message: AIMessage
    state_update: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallRequest:
    call: ToolCall
    tool: Tool | None
    state: dict[str, Any]


ModelHandler = Callable[[ModelRequest], ModelResponse]
ToolHandler = Callable[[ToolCallRequest], ToolMessage | Command]


class Middleware:
    tools: list[Tool] = []  # noqa: RUF012 - overridden per instance

    @property
    def name(self) -> str:
        return type(self).__name__

    def before_agent(self, state: dict[str, Any]) -> dict[str, Any] | None:  # noqa: ARG002
        return None

    def wrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        return handler(request)

    def wrap_tool_call(self, request: ToolCallRequest, handler: ToolHandler) -> ToolMessage | Command:
        return handler(request)


def append_system(request: ModelRequest, section: str) -> ModelRequest:
    """Append a section to the per-call system prompt (deepagents `append_to_system_message`)."""
    prompt = f"{request.system_prompt}\n\n{section}" if request.system_prompt else section
    return request.override(system_prompt=prompt)


def with_update(response: ModelResponse, update: dict[str, Any]) -> ModelResponse:
    return replace(response, state_update={**response.state_update, **update})
