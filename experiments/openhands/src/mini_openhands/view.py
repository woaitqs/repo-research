"""View: the LLM-facing projection of the event log.

Mirrors openhands-sdk/openhands/sdk/context/view/view.py and
context/view/properties/*. The view is *derived* state:
* Condensation events are applied (forgotten ids removed, summary inserted).
* CondensationRequest only flips a flag.
* Non-LLM-convertible events are skipped.

`manipulation_indices` are the cut points where a condenser may slice the
view without separating a tool call from its result (the SDK enforces
tool-call matching, batch atomicity and tool-loop atomicity this way).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import (
    ActionEvent,
    AgentErrorEvent,
    Condensation,
    CondensationRequest,
    Event,
    ObservationEvent,
    SystemPromptEvent,
    UserRejectObservation,
    events_to_messages,
)

_RESULT_TYPES = (ObservationEvent, AgentErrorEvent, UserRejectObservation)


@dataclass
class View:
    events: list[Event] = field(default_factory=list)
    unhandled_condensation_request: bool = False

    def __len__(self) -> int:
        return len(self.events)

    def append_event(self, event: Event) -> None:
        if isinstance(event, Condensation):
            self.events = event.apply(self.events)
            self.unhandled_condensation_request = False
        elif isinstance(event, CondensationRequest):
            self.unhandled_condensation_request = True
        elif event.llm_convertible:
            self.events.append(event)
        # anything else (errors, state artifacts) never reaches the model

    @staticmethod
    def from_events(events: list[Event]) -> "View":
        view = View()
        for event in events:
            view.append_event(event)
        return view

    def manipulation_indices(self) -> list[int]:
        """Indices i (0..len) such that cutting at i keeps every tool call in
        the same side as its result."""
        valid = []
        open_calls: set[str] = set()
        for i, event in enumerate(self.events):
            if not open_calls:
                valid.append(i)
            if isinstance(event, ActionEvent):
                open_calls.add(event.tool_call_id)
            elif isinstance(event, _RESULT_TYPES):
                open_calls.discard(event.tool_call_id)
        if not open_calls:
            valid.append(len(self.events))
        return valid

    def next_manipulation_index(self, at_least: int) -> int:
        for idx in self.manipulation_indices():
            if idx >= at_least:
                return idx
        return len(self.events)

    def system_prompt_index(self) -> int | None:
        for i, event in enumerate(self.events):
            if isinstance(event, SystemPromptEvent):
                return i
        return None

    def to_messages(self) -> list[dict[str, Any]]:
        return events_to_messages(self.events)
