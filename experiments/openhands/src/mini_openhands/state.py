"""ConversationState: small mutable snapshot + append-only event log.

Mirrors openhands-sdk/openhands/sdk/conversation/state.py:
* `base_state.json` holds the few mutable fields (status, agent spec, ...)
  and is rewritten whenever a persisted field changes (autosave in
  __setattr__, like the SDK).
* `events/` holds the immutable history (EventLog).
* `view` is a cached projection, extended incrementally on append and rebuilt
  from scratch on resume.
* `get_unmatched_actions()` finds executable actions without a result; the
  agent uses it for confirmation mode and crash recovery.
"""

from __future__ import annotations

import json
import threading
from enum import Enum
from pathlib import Path
from typing import Any

from .event_log import EventLog
from .events import (
    ActionEvent,
    AgentErrorEvent,
    Event,
    ObservationEvent,
    UserRejectObservation,
)
from .view import View

BASE_STATE = "base_state.json"
EVENTS_DIR = "events"


class Status(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    FINISHED = "finished"
    ERROR = "error"
    STUCK = "stuck"


_PERSISTED = ("id", "status", "agent_spec", "confirmation_mode", "max_iterations")


class ConversationState:
    def __init__(self, conversation_id: str, root: Path | None) -> None:
        self._autosave = False
        self._root = root
        self.id = conversation_id
        self.status = Status.IDLE
        self.agent_spec: dict[str, Any] = {}
        self.confirmation_mode = False
        self.max_iterations = 50
        self.lock = threading.RLock()
        self.events = EventLog(root / EVENTS_DIR if root else None)
        self._view = View()
        self._view_len = 0

    # ---------------------------------------------------------------- create / resume
    @classmethod
    def create(cls, conversation_id: str, persistence_dir: Path | None) -> "ConversationState":
        root = persistence_dir / conversation_id if persistence_dir else None
        state = cls(conversation_id, root)
        base = root / BASE_STATE if root else None
        if base is not None and base.exists():  # resume path
            data = json.loads(base.read_text(encoding="utf-8"))
            state.status = Status(data["status"])
            state.agent_spec = data["agent_spec"]
            state.confirmation_mode = data["confirmation_mode"]
            state.max_iterations = data["max_iterations"]
        state._autosave = True
        state._save()
        return state

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def observations_dir(self) -> Path | None:
        return self._root / "observations" if self._root else None

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name in _PERSISTED and getattr(self, "_autosave", False):
            self._save()

    def _save(self) -> None:
        if self._root is None:
            return
        payload = {k: getattr(self, k) for k in _PERSISTED}
        payload["status"] = self.status.value
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / BASE_STATE).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------- events
    def append_event(self, event: Event) -> None:
        """Single persistence chokepoint (the SDK's default callback)."""
        self.events.append(event)

    @property
    def view(self) -> View:
        """Incremental projection: replay only the events appended since the
        last read (O(k)), like ConversationState.view in the SDK."""
        if self._view_len < len(self.events):
            for i in range(self._view_len, len(self.events)):
                self._view.append_event(self.events[i])
            self._view_len = len(self.events)
        return self._view

    def get_unmatched_actions(self) -> list[ActionEvent]:
        observed_action_ids: set[str] = set()
        observed_call_ids: set[str] = set()
        pending: list[ActionEvent] = []
        for event in reversed(list(self.events)):
            if isinstance(event, (ObservationEvent, UserRejectObservation)):
                observed_action_ids.add(event.action_id)
            elif isinstance(event, AgentErrorEvent):
                observed_call_ids.add(event.tool_call_id)
            elif isinstance(event, ActionEvent):
                if (
                    event.arguments is not None
                    and event.id not in observed_action_ids
                    and event.tool_call_id not in observed_call_ids
                ):
                    pending.insert(0, event)
        return pending
