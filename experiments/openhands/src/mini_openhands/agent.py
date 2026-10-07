"""Stateless Agent: configuration + one `step()`.

Mirrors openhands-sdk/openhands/sdk/agent/agent.py (Agent.step / _step,
_get_action_event, _execute_action_event, _requires_user_confirmation) and
agent/response_dispatch.py (classify_response + handlers).

The agent holds *no* conversation history. Each step it:
  1. executes pending (confirmed) actions, if any, and returns;
  2. lets the condenser look at `state.view`; a Condensation is emitted and
     the step returns (the next step sees the condensed view);
  3. calls the LLM with the projected messages + tool schemas;
  4. turns tool calls into ActionEvents (bad calls -> AgentErrorEvent);
  5. stops for confirmation if policy requires it, else executes;
  6. emits ObservationEvents; `finish` or a plain text answer ends the run.
Every effect leaves the agent through `on_event`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .condenser import SummarizingCondenser
from .events import (
    ActionEvent,
    AgentErrorEvent,
    Condensation,
    CondensationRequest,
    Event,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from .llm import LLM, ContextWindowExceededError, LLMResponse
from .state import Status
from .tools import BUILT_IN_TOOLS, ToolDefinition, ToolSpec, resolve_tool

if TYPE_CHECKING:
    from .conversation import Conversation

OnEvent = Callable[[Event], None]

DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent working inside a sandboxed workspace. Use the tools "
    "to inspect and change files. Call `finish` with a short message when done."
)
NUDGE = "Your last response had neither a tool call nor a message. Use a tool to proceed."


@dataclass
class Agent:
    llm: LLM
    tools: list[ToolSpec] = field(default_factory=list)
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    condenser: SummarizingCondenser | None = None
    _tools: dict[str, ToolDefinition] = field(default_factory=dict, init=False, repr=False)

    # ------------------------------------------------------------ init / verify
    def spec(self) -> dict[str, Any]:
        return {"tools": [t.name for t in self.tools], "system_prompt": self.system_prompt}

    def verify(self, persisted: dict[str, Any]) -> None:
        """Resume rule from AgentBase.verify(): tools may be added, never removed
        (the model may already have been told about them)."""
        removed = set(persisted.get("tools", [])) - {t.name for t in self.tools}
        if removed:
            raise ValueError(f"cannot resume: tools removed mid-conversation {sorted(removed)}")

    def init_state(self, conversation: "Conversation", on_event: OnEvent) -> None:
        if not self._tools:
            resolved: list[ToolDefinition] = []
            for spec in self.tools:
                resolved.extend(resolve_tool(spec))
            resolved.extend(BUILT_IN_TOOLS)
            names = [t.name for t in resolved]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate tool names: {names}")
            self._tools = {t.name: t for t in resolved}
        if any(isinstance(e, SystemPromptEvent) for e in list(conversation.state.events)[:3]):
            return  # resumed conversation: system prompt already recorded
        on_event(
            SystemPromptEvent(
                system_prompt=self.system_prompt,
                tools=[t.to_openai_tool() for t in self._tools.values()],
            )
        )

    @property
    def tools_map(self) -> dict[str, ToolDefinition]:
        return self._tools

    # ------------------------------------------------------------ the step
    def step(self, conversation: "Conversation", on_event: OnEvent) -> None:
        state = conversation.state

        pending = state.get_unmatched_actions()
        if pending:  # confirmation mode: the second run() is the approval
            self._execute(conversation, pending, on_event)
            return

        view = state.view
        if self.condenser is not None:
            condensed = self.condenser.condense(view)
            if isinstance(condensed, Condensation):
                on_event(condensed)
                return
            view = condensed

        try:
            response = self.llm.complete(
                view.to_messages(), tools=[t.to_openai_tool() for t in self._tools.values()]
            )
        except ContextWindowExceededError:
            if self.condenser is None:
                raise
            on_event(CondensationRequest())
            return

        if response.tool_calls:
            actions = self._to_action_events(response, on_event)
            if self._requires_confirmation(conversation, actions):
                state.status = Status.WAITING_FOR_CONFIRMATION
                return
            self._execute(conversation, actions, on_event)
        elif response.text.strip():
            on_event(MessageEvent(source="agent", text=response.text))
            state.status = Status.FINISHED
        else:
            on_event(MessageEvent(source="environment", text=NUDGE))

    # ------------------------------------------------------------ helpers
    def _to_action_events(self, response: LLMResponse, on_event: OnEvent) -> list[ActionEvent]:
        actions: list[ActionEvent] = []
        for i, call in enumerate(response.tool_calls):
            thought = response.text if i == 0 else ""
            tool = self._tools.get(call.name)
            try:
                if tool is None:
                    raise LookupError(
                        f"Tool '{call.name}' not found. Available: {list(self._tools)}"
                    )
                args = json.loads(call.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("tool arguments must be a JSON object")
                tool.validate(args)
            except (LookupError, ValueError) as exc:  # JSONDecodeError is a ValueError
                # Keep the call (arguments=None => not executable) so the
                # tool_call/tool_result pairing stays valid, then answer it with
                # an error the model can read and fix.
                on_event(
                    ActionEvent(
                        tool_name=call.name,
                        tool_call_id=call.id,
                        raw_arguments=json.dumps({"_malformed": True}),
                        arguments=None,
                        llm_response_id=response.id,
                        thought=thought,
                    )
                )
                on_event(
                    AgentErrorEvent(
                        tool_name=call.name,
                        tool_call_id=call.id,
                        error=f"Error validating tool '{call.name}': {exc}",
                    )
                )
                continue
            action = ActionEvent(
                tool_name=call.name,
                tool_call_id=call.id,
                raw_arguments=json.dumps(args),
                arguments=args,
                llm_response_id=response.id,
                thought=thought,
            )
            on_event(action)
            actions.append(action)
        return actions

    def _requires_confirmation(self, conversation: "Conversation", actions: list[ActionEvent]) -> bool:
        if not conversation.state.confirmation_mode or not actions:
            return False
        return any(not self._tools[a.tool_name].read_only for a in actions)

    def _execute(self, conversation: "Conversation", actions: list[ActionEvent], on_event: OnEvent) -> None:
        finished = False
        for action in actions:
            tool = self._tools[action.tool_name]
            assert action.arguments is not None
            try:
                result = tool.executor(action.arguments, conversation)
            except (ValueError, OSError) as exc:
                on_event(
                    AgentErrorEvent(
                        tool_name=tool.name,
                        tool_call_id=action.tool_call_id,
                        error=f"Error executing tool '{tool.name}': {exc}",
                    )
                )
                continue
            on_event(
                ObservationEvent(
                    action_id=action.id,
                    tool_name=tool.name,
                    tool_call_id=action.tool_call_id,
                    content=result.content,
                    is_error=result.is_error,
                )
            )
            finished = finished or tool.name == "finish"
        if finished:
            conversation.state.status = Status.FINISHED
