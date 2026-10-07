"""Model abstraction.

deepagents accepts any LangChain `BaseChatModel` (or a `provider:model` string resolved
by `deepagents/_models.py::resolve_model`). The harness only needs three things from a
model: `invoke(messages, tools)`, an optional context-window size (LangChain's
`model.profile["max_input_tokens"]`, used by summarization defaults), and a way to
signal "context too long" (`langchain_core.exceptions.ContextOverflowError`).

`ScriptedModel` is a deterministic stand-in so the architecture can be exercised and
tested without network access or API keys.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from minideep.messages import AIMessage, HumanMessage, Message, count_tokens
from minideep.tools import Tool


class ContextOverflowError(Exception):
    """Raised by a model when the request exceeds its context window."""


class ChatModel(Protocol):
    max_input_tokens: int | None

    def invoke(self, messages: list[Message], tools: Sequence[Tool]) -> AIMessage: ...


Script = Callable[[list[Message], list[str], int], AIMessage]


@dataclass
class ModelCall:
    messages: list[Message]
    tool_names: list[str]


@dataclass
class ScriptedModel:
    """Fake chat model driven by a script; records every request it receives.

    `script(messages, tool_names, call_number)` returns the next `AIMessage`.
    If `max_input_tokens` is set and a request exceeds it, the model raises
    `ContextOverflowError`, like a provider rejecting an oversized prompt.
    """

    script: Script
    max_input_tokens: int | None = None
    calls: list[ModelCall] = field(default_factory=list)

    def invoke(self, messages: list[Message], tools: Sequence[Tool]) -> AIMessage:
        names = [t.name for t in tools]
        self.calls.append(ModelCall(list(messages), names))
        if self.max_input_tokens is not None and count_tokens(messages) > self.max_input_tokens:
            msg = f"prompt is {count_tokens(messages)} tokens, limit {self.max_input_tokens}"
            raise ContextOverflowError(msg)
        return self.script(list(messages), names, len(self.calls))


def is_summary_request(messages: list[Message]) -> bool:
    """True when the summarization middleware is asking the model for a summary."""
    return len(messages) == 1 and isinstance(messages[0], HumanMessage) and "<messages>" in messages[0].content
