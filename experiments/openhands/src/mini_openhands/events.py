"""Typed, immutable events: the single source of truth of a conversation.

Mirrors openhands-sdk/openhands/sdk/event/ (Event, LLMConvertibleEvent,
ActionEvent, ObservationEvent, AgentErrorEvent, Condensation, ...).

Two families matter:
* LLM-convertible events know how to become one chat message (`to_llm_message`).
* Control events (Condensation, CondensationRequest, ConversationErrorEvent)
  are persisted but never sent to the model; the View interprets them.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

EVENT_TYPES: dict[str, type["Event"]] = {}


def new_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass(frozen=True, kw_only=True)
class Event:
    id: str = field(default_factory=new_id)
    source: str = "environment"  # "user" | "agent" | "environment"

    llm_convertible: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        EVENT_TYPES[cls.__name__] = cls

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = type(self).__name__  # discriminator, like the SDK's `kind`
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Event":
        payload = dict(data)
        cls = EVENT_TYPES[payload.pop("kind")]
        return cls(**payload)

    def to_llm_message(self) -> dict[str, Any]:  # pragma: no cover - overridden
        raise TypeError(f"{type(self).__name__} is not LLM-convertible")


# ---------------------------------------------------------------- LLM-convertible


@dataclass(frozen=True, kw_only=True)
class SystemPromptEvent(Event):
    system_prompt: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    source: str = "agent"
    llm_convertible: ClassVar[bool] = True

    def to_llm_message(self) -> dict[str, Any]:
        return {"role": "system", "content": self.system_prompt}


@dataclass(frozen=True, kw_only=True)
class MessageEvent(Event):
    """A chat turn. source='user' (human), 'agent' (final answer) or
    'environment' (framework nudge sent with role=user)."""

    text: str
    llm_convertible: ClassVar[bool] = True

    def to_llm_message(self) -> dict[str, Any]:
        role = "assistant" if self.source == "agent" else "user"
        return {"role": role, "content": self.text}


@dataclass(frozen=True, kw_only=True)
class ActionEvent(Event):
    """One tool call. `arguments is None` means the call failed validation and
    is *not executable* (it is still kept so the tool_call/tool_result pairing
    the provider expects stays intact)."""

    tool_name: str
    tool_call_id: str
    raw_arguments: str
    arguments: dict[str, Any] | None
    llm_response_id: str
    thought: str = ""
    source: str = "agent"
    llm_convertible: ClassVar[bool] = True

    def to_llm_message(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": self.thought,
            "tool_calls": [self.tool_call_dict()],
        }

    def tool_call_dict(self) -> dict[str, Any]:
        return {
            "id": self.tool_call_id,
            "type": "function",
            "function": {"name": self.tool_name, "arguments": self.raw_arguments},
        }


@dataclass(frozen=True, kw_only=True)
class ObservationEvent(Event):
    action_id: str
    tool_name: str
    tool_call_id: str
    content: str
    is_error: bool = False
    llm_convertible: ClassVar[bool] = True

    def to_llm_message(self) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": self.tool_call_id, "content": self.content}


@dataclass(frozen=True, kw_only=True)
class AgentErrorEvent(Event):
    """Scaffold-level error (unknown tool, bad JSON, tool ValueError). It is an
    observation *for the model*: the loop continues and the LLM self-corrects."""

    tool_name: str
    tool_call_id: str
    error: str
    source: str = "agent"
    llm_convertible: ClassVar[bool] = True

    def to_llm_message(self) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": self.tool_call_id, "content": self.error}


@dataclass(frozen=True, kw_only=True)
class UserRejectObservation(Event):
    action_id: str
    tool_name: str
    tool_call_id: str
    reason: str = "User rejected the action"
    source: str = "user"
    llm_convertible: ClassVar[bool] = True

    def to_llm_message(self) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": f"Action rejected: {self.reason}",
        }


@dataclass(frozen=True, kw_only=True)
class CondensationSummaryEvent(Event):
    """Never stored: synthesized by the View from a Condensation."""

    summary: str
    llm_convertible: ClassVar[bool] = True

    def to_llm_message(self) -> dict[str, Any]:
        return {"role": "user", "content": self.summary}


# ---------------------------------------------------------------- control events


@dataclass(frozen=True, kw_only=True)
class Condensation(Event):
    """'Forget these event ids (and optionally insert a summary at offset)'.
    Appending this event is how context is compacted: nothing is deleted."""

    forgotten_event_ids: list[str]
    summary: str | None = None
    summary_offset: int | None = None

    def apply(self, events: list[Event]) -> list[Event]:
        forgotten = set(self.forgotten_event_ids)
        kept = [e for e in events if e.id not in forgotten]
        if self.summary is not None and self.summary_offset is not None:
            kept.insert(
                self.summary_offset,
                CondensationSummaryEvent(id=f"{self.id}-summary", summary=self.summary),
            )
        return kept


@dataclass(frozen=True, kw_only=True)
class CondensationRequest(Event):
    """Emitted when the provider reports context-window overflow."""


@dataclass(frozen=True, kw_only=True)
class ConversationErrorEvent(Event):
    code: str
    detail: str


def events_to_messages(events: list[Event]) -> list[dict[str, Any]]:
    """Project LLM-convertible events to chat messages.

    Consecutive ActionEvents from the same LLM response are re-merged into one
    assistant message with several tool_calls (the SDK stores one event per
    tool call but must replay the original parallel-call message)."""
    messages: list[dict[str, Any]] = []
    i = 0
    while i < len(events):
        event = events[i]
        if isinstance(event, ActionEvent):
            batch = [event]
            j = i + 1
            while (
                j < len(events)
                and isinstance(events[j], ActionEvent)
                and events[j].llm_response_id == event.llm_response_id
            ):
                batch.append(events[j])
                j += 1
            messages.append(
                {
                    "role": "assistant",
                    "content": batch[0].thought,
                    "tool_calls": [a.tool_call_dict() for a in batch],
                }
            )
            i = j
            continue
        message = event.to_llm_message()
        if messages and _is_plain_user(messages[-1]) and _is_plain_user(message):
            # e.g. user turn + condensation summary: coalesce into one user turn
            messages[-1] = {"role": "user", "content": messages[-1]["content"] + "\n\n" + message["content"]}
        else:
            messages.append(message)
        i += 1
    return messages


def _is_plain_user(message: dict[str, Any]) -> bool:
    return message["role"] == "user" and "tool_calls" not in message and "tool_call_id" not in message
