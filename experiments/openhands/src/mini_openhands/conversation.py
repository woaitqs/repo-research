"""Conversation: owns the loop, the lock, the status machine and persistence.

Mirrors openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py:
* the default callback persists every event *before* user callbacks run
  (so no subscriber hears about an event that is not on disk);
* `send_message` resets FINISHED/STUCK to IDLE so a new turn can run;
* `run()` loops `agent.step()` under the state lock until FINISHED, PAUSED,
  STUCK, WAITING_FOR_CONFIRMATION, an error, or max_iterations;
* the same constructor resumes a conversation from disk (create-or-resume).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

from .agent import Agent
from .events import ConversationErrorEvent, Event, MessageEvent, UserRejectObservation
from .state import ConversationState, Status
from .stuck import is_stuck
from .workspace import LocalWorkspace, Workspace

Callback = Callable[[Event], None]


class Conversation:
    def __init__(
        self,
        agent: Agent,
        workspace: Workspace | str | Path,
        persistence_dir: str | Path | None = None,
        conversation_id: str | None = None,
        callbacks: list[Callback] | None = None,
        max_iterations: int = 50,
        confirmation_mode: bool = False,
        stuck_threshold: int = 4,
    ) -> None:
        self.agent = agent
        self.workspace = workspace if not isinstance(workspace, (str, Path)) else LocalWorkspace(workspace)
        self.state = ConversationState.create(
            conversation_id or uuid.uuid4().hex,
            Path(persistence_dir) if persistence_dir else None,
        )
        resumed = bool(self.state.agent_spec)
        if resumed:
            agent.verify(self.state.agent_spec)
        else:
            self.state.confirmation_mode = confirmation_mode
            self.state.max_iterations = max_iterations
        self.state.agent_spec = agent.spec()
        self.stuck_threshold = stuck_threshold
        user_callbacks = list(callbacks or [])

        def on_event(event: Event) -> None:
            self.state.append_event(event)  # persist first
            for cb in user_callbacks:
                cb(event)

        self._on_event = on_event
        self._ready = False

    @property
    def id(self) -> str:
        return self.state.id

    def _ensure_agent_ready(self) -> None:
        if not self._ready:
            with self.state.lock:
                self.agent.init_state(self, self._on_event)
                self._ready = True

    # ------------------------------------------------------------ public API
    def send_message(self, text: str) -> None:
        self._ensure_agent_ready()
        with self.state.lock:
            if self.state.status in (Status.FINISHED, Status.STUCK):
                self.state.status = Status.IDLE
            self._on_event(MessageEvent(source="user", text=text))

    def reject_pending_actions(self, reason: str = "User rejected the action") -> None:
        with self.state.lock:
            if self.state.status == Status.WAITING_FOR_CONFIRMATION:
                self.state.status = Status.IDLE
            for action in self.state.get_unmatched_actions():
                self._on_event(
                    UserRejectObservation(
                        action_id=action.id,
                        tool_name=action.tool_name,
                        tool_call_id=action.tool_call_id,
                        reason=reason,
                    )
                )

    def pause(self) -> None:
        with self.state.lock:
            self.state.status = Status.PAUSED

    def run(self) -> None:
        self._ensure_agent_ready()
        with self.state.lock:
            if self.state.status in (Status.IDLE, Status.PAUSED, Status.ERROR, Status.STUCK):
                self.state.status = Status.RUNNING
        iteration = 0
        try:
            while True:
                with self.state.lock:
                    status = self.state.status
                    if status in (Status.PAUSED, Status.STUCK, Status.FINISHED):
                        break
                    if is_stuck(list(self.state.events), self.stuck_threshold):
                        self.state.status = Status.STUCK
                        break
                    if status == Status.WAITING_FOR_CONFIRMATION:
                        self.state.status = Status.RUNNING  # calling run() again = approval
                    self.agent.step(self, self._on_event)
                    iteration += 1
                    if self.state.status == Status.WAITING_FOR_CONFIRMATION:
                        break
                    if iteration >= self.state.max_iterations and self.state.status != Status.FINISHED:
                        self.state.status = Status.ERROR
                        self._on_event(
                            ConversationErrorEvent(
                                code="MaxIterationsReached",
                                detail=f"reached {self.state.max_iterations} iterations",
                            )
                        )
                        break
        except Exception as exc:
            with self.state.lock:
                self.state.status = Status.ERROR
                self._on_event(ConversationErrorEvent(code=type(exc).__name__, detail=str(exc)))
            raise
