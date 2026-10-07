"""Runner: one while-loop over a four-state `NextStep` machine.

Upstream map (commit 71c2da4):

* `Runner.run` -> `AgentRunner._run_impl` with its `while True` (`src/agents/run.py:1026`).
* One turn = `run_single_turn` (`src/agents/run_internal/run_loop.py:2665`):
  resolve instructions/tools/handoffs -> build input -> `call_model_input_filter`
  -> `model.get_response` -> `get_single_step_result_from_response`.
* `process_model_response` classifies output items (`turn_resolution.py:2926`).
* `execute_tools_and_side_effects` runs tools and picks the next step (`turn_resolution.py:804`).
* `NextStepRunAgain | NextStepHandoff | NextStepFinalOutput | NextStepInterruption`
  (`src/agents/run_internal/run_steps.py:164-191`).
* `RunState` is the serializable pause point for approvals (`src/agents/run_state.py:835`).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from .agent import (
    Agent,
    Handoff,
    InputGuardrail,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
)
from .capabilities import CapabilityRuntime
from .items import (
    Item,
    RunItem,
    function_call_output,
    input_to_list,
    prepare_model_input,
    text_of,
)
from .model import Model, ModelResponse
from .session import Session
from .tool import DEFAULT_REJECTION, FunctionTool


class MaxTurnsExceeded(Exception):
    pass


class ModelBehaviorError(Exception):
    """The model did something the runtime cannot honour (e.g. called an unknown tool)."""


# ------------------------------------------------------------------------ configuration ---


@dataclass
class ModelInputData:
    input: list[Item]
    instructions: str | None


@dataclass
class HandoffInputData:
    input_history: tuple[Item, ...]
    pre_handoff_items: tuple[RunItem, ...]
    new_items: tuple[RunItem, ...]


@dataclass
class RunConfig:
    model: Model | None = None
    """Overrides every agent's model (upstream `RunConfig.model`)."""
    call_model_input_filter: Callable[[ModelInputData], ModelInputData | Awaitable[ModelInputData]] | None = None
    """Last hook before the model call; the place for trimming or redaction."""
    tool_not_found_behavior: Literal["raise_error", "return_error_to_model"] = "raise_error"
    nest_handoff_history: bool = False
    handoff_input_filter: Callable[[HandoffInputData], HandoffInputData] | None = None
    input_guardrails: list[InputGuardrail] = field(default_factory=list)
    workspace: Any = None
    """A `capabilities.Workspace` for `CapableAgent`s (upstream `RunConfig.sandbox`)."""


@dataclass
class RunContext:
    """Local runtime state. Never sent to the model (upstream `RunContextWrapper`)."""

    context: Any = None
    run_config: RunConfig | None = None
    usage: dict[str, int] = field(default_factory=lambda: {"requests": 0})
    approvals: dict[str, bool] = field(default_factory=dict)
    rejection_messages: dict[str, str] = field(default_factory=dict)

    def approve(self, call_id: str) -> None:
        self.approvals[call_id] = True

    def reject(self, call_id: str, message: str | None = None) -> None:
        self.approvals[call_id] = False
        if message:
            self.rejection_messages[call_id] = message


# ----------------------------------------------------------------------- state machine ---


@dataclass
class NextStepRunAgain:
    pass


@dataclass
class NextStepHandoff:
    new_agent: Agent


@dataclass
class NextStepFinalOutput:
    output: Any


@dataclass
class NextStepInterruption:
    interruptions: list[RunItem]


NextStep = NextStepRunAgain | NextStepHandoff | NextStepFinalOutput | NextStepInterruption


@dataclass
class ToolRun:
    call: Item
    tool: FunctionTool


@dataclass
class ProcessedResponse:
    new_items: list[RunItem]
    functions: list[ToolRun]
    handoffs: list[tuple[Item, Handoff]]
    not_found: list[Item]
    message_text: str | None


@dataclass
class SingleStepResult:
    original_input: str | list[Item]
    pre_step_items: list[RunItem]
    new_step_items: list[RunItem]
    next_step: NextStep
    session_step_items: list[RunItem] | None = None
    """Unfiltered items for history when a handoff filter changed the model view."""


# --------------------------------------------------------------------------- run state ---


def _agent_graph(start: Agent) -> dict[str, Agent]:
    seen: dict[str, Agent] = {}
    stack = [start]
    while stack:
        agent = stack.pop()
        if agent.name not in seen:
            seen[agent.name] = agent
            stack.extend(h.agent for h in agent.get_handoffs())
    return seen


@dataclass
class RunState:
    """Everything needed to resume a paused run, possibly in another process."""

    context: RunContext
    starting_agent: Agent
    current_agent: Agent
    original_input: str | list[Item]
    max_turns: int | None
    current_turn: int = 0
    generated_items: list[RunItem] = field(default_factory=list)
    """Model-view items generated so far (may be filtered by a handoff)."""
    session_items: list[RunItem] = field(default_factory=list)
    """Every settled item, for results and session history."""
    current_step: NextStepInterruption | None = None
    turn_start: int = 0
    """Index in `generated_items` where the paused turn begins."""

    def get_interruptions(self) -> list[RunItem]:
        return list(self.current_step.interruptions) if self.current_step else []

    def approve(self, item: RunItem) -> None:
        self.context.approve(item.item["call_id"])

    def reject(self, item: RunItem, message: str | None = None) -> None:
        self.context.reject(item.item["call_id"], message)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": "mini-1",
            "current_turn": self.current_turn,
            "max_turns": self.max_turns,
            "current_agent": self.current_agent.name,
            "original_input": self.original_input,
            "generated_items": [i.to_json() for i in self.generated_items],
            "session_items": [i.to_json() for i in self.session_items],
            "interruptions": [i.to_json() for i in self.get_interruptions()],
            "turn_start": self.turn_start,
            "context": {
                "context": self.context.context,
                "usage": self.context.usage,
                "approvals": self.context.approvals,
                "rejection_messages": self.context.rejection_messages,
            },
        }

    @classmethod
    def from_json(cls, starting_agent: Agent, data: dict[str, Any]) -> RunState:
        """Agents are code, not data: they are re-bound by name from the caller's graph."""
        agents = _agent_graph(starting_agent)
        ctx = RunContext(
            context=data["context"]["context"],
            usage=dict(data["context"]["usage"]),
            approvals=dict(data["context"]["approvals"]),
            rejection_messages=dict(data["context"]["rejection_messages"]),
        )
        interruptions = [RunItem.from_json(i) for i in data["interruptions"]]
        return cls(
            context=ctx,
            starting_agent=starting_agent,
            current_agent=agents[data["current_agent"]],
            original_input=data["original_input"],
            max_turns=data["max_turns"],
            current_turn=data["current_turn"],
            generated_items=[RunItem.from_json(i) for i in data["generated_items"]],
            session_items=[RunItem.from_json(i) for i in data["session_items"]],
            current_step=NextStepInterruption(interruptions) if interruptions else None,
            turn_start=data["turn_start"],
        )


@dataclass
class RunResult:
    input: str | list[Item]
    new_items: list[RunItem]
    final_output: Any
    last_agent: Agent
    interruptions: list[RunItem]
    state: RunState

    def to_state(self) -> RunState:
        return self.state

    def to_input_list(self) -> list[Item]:
        """Manual memory: feed this into the next `Runner.run` call."""
        return prepare_model_input(self.input, self.new_items)


# ------------------------------------------------------------------------------ runner ---


class Runner:
    @classmethod
    async def run(
        cls,
        starting_agent: Agent,
        input: str | list[Item] | RunState,
        *,
        context: Any = None,
        max_turns: int | None = 10,
        run_config: RunConfig | None = None,
        session: Session | None = None,
    ) -> RunResult:
        config = run_config or RunConfig()
        resuming = isinstance(input, RunState)
        if isinstance(input, RunState):
            state = input
            state.context.run_config = config
        else:
            new_input = input_to_list(input)
            history = await session.get_items(session.settings.limit) if session is not None else []
            state = RunState(
                context=RunContext(context=context, run_config=config),
                starting_agent=starting_agent,
                current_agent=starting_agent,
                original_input=history + new_input if session is not None else input,
                max_turns=max_turns,
            )
            if session is not None:
                # Upstream saves the new user input before the first model turn (run.py:1007).
                await session.add_items(new_input)

        agent = state.current_agent
        runtime = CapabilityRuntime(config.workspace)
        while True:
            # Public agent vs execution agent: capabilities prepare a per-run clone with extra
            # tools/instructions; results and handoffs keep the public identity.
            prepared = runtime.prepare(agent)
            state.original_input = prepared.process_input(state.original_input)
            exec_agent = prepared.execution
            if state.current_step is not None:
                # Resuming an interruption continues the paused turn: no new turn is charged.
                turn = await resolve_interrupted_turn(exec_agent, state, config)
            else:
                state.current_turn += 1
                if state.max_turns is not None and state.current_turn > state.max_turns:
                    raise MaxTurnsExceeded(f"Max turns ({state.max_turns}) exceeded")
                if state.current_turn == 1 and not resuming:
                    turn = await _first_turn_with_input_guardrails(exec_agent, state, config)
                else:
                    turn = await run_single_turn(exec_agent, state, config)

            state.original_input = turn.original_input
            state.generated_items = turn.pre_step_items + turn.new_step_items
            turn_session_items = turn.session_step_items or turn.new_step_items
            step = turn.next_step

            if isinstance(step, NextStepInterruption):
                # Hold the paused turn: nothing is persisted until approvals settle it
                # (upstream RunState schema 1.19 "held pending session write").
                state.current_step = step
                state.turn_start = len(turn.pre_step_items)
                return _result(state, agent, None, step.interruptions, state.session_items + turn_session_items)

            state.current_step = None
            state.session_items.extend(turn_session_items)
            if session is not None:
                await session.add_items([i for i in (r.to_input() for r in turn_session_items) if i])

            if isinstance(step, NextStepFinalOutput):
                await _run_output_guardrails(agent, state, step.output)
                return _result(state, agent, step.output, [], state.session_items)
            if isinstance(step, NextStepHandoff):
                agent = step.new_agent
                state.current_agent = agent
            # NextStepRunAgain: loop.


def _result(state: RunState, agent: Agent, final: Any, interruptions: list[RunItem], items: list[RunItem]) -> RunResult:
    return RunResult(state.original_input, list(items), final, agent, list(interruptions), state)


async def _run_input_guardrails(guards: list[InputGuardrail], agent: Agent, state: RunState) -> None:
    items = input_to_list(state.original_input)
    results = await asyncio.gather(*(g.fn(state.context, agent, items) for g in guards))
    for result in results:
        if result.tripwire_triggered:
            raise InputGuardrailTripwireTriggered(result.output_info)


async def _first_turn_with_input_guardrails(agent: Agent, state: RunState, config: RunConfig) -> SingleStepResult:
    """Only the starting agent's guardrails, only on the first turn (upstream run.py:1040-1060).

    Blocking guardrails finish before the model is called. Parallel ones race the whole turn;
    on a tripwire the turn task is cancelled. Like upstream, a slow parallel guardrail can
    lose the race against the model call *and* tool side effects.
    """
    guards = agent.input_guardrails + config.input_guardrails
    blocking = [g for g in guards if not g.run_in_parallel]
    parallel = [g for g in guards if g.run_in_parallel]
    if blocking:
        await _run_input_guardrails(blocking, agent, state)
    turn_task = asyncio.create_task(run_single_turn(agent, state, config))
    if not parallel:
        return await turn_task
    guard_task = asyncio.create_task(_run_input_guardrails(parallel, agent, state))
    try:
        _, turn = await asyncio.gather(guard_task, turn_task)
        return turn
    except BaseException:
        for task in (guard_task, turn_task):
            task.cancel()
        await asyncio.gather(guard_task, turn_task, return_exceptions=True)
        raise


async def _run_output_guardrails(agent: Agent, state: RunState, output: Any) -> None:
    for guard in agent.output_guardrails:
        result = await guard.fn(state.context, agent, output)
        if result.tripwire_triggered:
            raise OutputGuardrailTripwireTriggered(result.output_info)


# ---------------------------------------------------------------------------- one turn ---


async def run_single_turn(agent: Agent, state: RunState, config: RunConfig) -> SingleStepResult:
    ctx = state.context
    # Everything is resolved per turn: dynamic instructions see the current context.
    system = await agent.get_system_prompt(ctx)
    tools = list(agent.tools)
    handoffs = agent.get_handoffs()
    model_input = prepare_model_input(state.original_input, state.generated_items)

    data = ModelInputData(input=model_input, instructions=system)
    if config.call_model_input_filter is not None:
        maybe = config.call_model_input_filter(data)
        data = await maybe if inspect.isawaitable(maybe) else maybe

    settings = dict(agent.model_settings)
    if agent.reset_tool_choice and _agent_used_tool(agent, state.generated_items):
        # Upstream `maybe_reset_tool_choice`: avoid an infinite forced-tool loop.
        settings.pop("tool_choice", None)

    model = config.model or agent.model
    if model is None:
        raise ValueError(f"Agent {agent.name!r} has no model")
    response = await model.get_response(
        system_instructions=data.instructions,
        input=data.input,
        tools=tools,
        handoffs=handoffs,
        settings=settings,
    )
    ctx.usage["requests"] += 1
    processed = process_model_response(agent, tools, handoffs, response, config)
    return await execute_tools_and_side_effects(agent, state, processed, config)


def _agent_used_tool(agent: Agent, items: list[RunItem]) -> bool:
    return any(i.agent_name == agent.name and i.kind == "function_call" for i in items)


def process_model_response(
    agent: Agent,
    tools: list[FunctionTool],
    handoffs: list[Handoff],
    response: ModelResponse,
    config: RunConfig,
) -> ProcessedResponse:
    """Classify each output item. A handoff is recognised purely by its tool name."""
    tool_map = {t.name: t for t in tools}
    handoff_map = {h.tool_name: h for h in handoffs}
    processed = ProcessedResponse([], [], [], [], None)
    for item in response.output:
        processed.new_items.append(RunItem(item, agent.name))
        if item.get("type") == "message":
            processed.message_text = text_of(item)
        elif item.get("type") == "function_call":
            name = item["name"]
            if name in handoff_map:
                processed.handoffs.append((item, handoff_map[name]))
            elif name in tool_map:
                processed.functions.append(ToolRun(item, tool_map[name]))
            elif config.tool_not_found_behavior == "return_error_to_model":
                processed.not_found.append(item)
            else:
                raise ModelBehaviorError(f"Tool {name} not found in agent {agent.name}")
    return processed


async def _execute_function_tools(
    agent: Agent, state: RunState, runs: list[ToolRun]
) -> tuple[list[RunItem], list[RunItem]]:
    """Plan (approval) first, then execute approved/unguarded calls concurrently.

    Returns (output items in model order, approval placeholders).
    """
    ctx = state.context
    to_execute: list[ToolRun] = []
    outputs: dict[str, str] = {}
    interruptions: list[RunItem] = []
    for run in runs:
        call_id = run.call["call_id"]
        decision = ctx.approvals.get(call_id)
        if decision is None and run.tool.requires_approval(ctx, run.call["arguments"]):
            interruptions.append(RunItem(run.call, agent.name, {"approval": True, "tool": run.tool.name}))
        elif decision is False:
            outputs[call_id] = ctx.rejection_messages.get(call_id, DEFAULT_REJECTION)
        else:
            to_execute.append(run)
    # Concurrent execution (upstream `_FunctionToolBatchExecutor`), results in model order.
    results = await asyncio.gather(*(r.tool.invoke(ctx, r.call["arguments"]) for r in to_execute))
    outputs.update({r.call["call_id"]: out for r, out in zip(to_execute, results, strict=True)})
    ordered = [
        RunItem(function_call_output(r.call["call_id"], outputs[r.call["call_id"]]), agent.name)
        for r in runs
        if r.call["call_id"] in outputs
    ]
    return ordered, interruptions


async def execute_tools_and_side_effects(
    agent: Agent, state: RunState, processed: ProcessedResponse, config: RunConfig
) -> SingleStepResult:
    pre_step_items = list(state.generated_items)
    new_items = list(processed.new_items)

    outputs, interruptions = await _execute_function_tools(agent, state, processed.functions)
    new_items.extend(outputs)
    for call in processed.not_found:
        new_items.append(
            RunItem(function_call_output(call["call_id"], f"Tool '{call['name']}' not found."), agent.name)
        )

    if interruptions:
        return SingleStepResult(
            state.original_input, pre_step_items, new_items + interruptions, NextStepInterruption(interruptions)
        )
    if processed.handoffs:
        return await execute_handoff(agent, state, pre_step_items, new_items, processed.handoffs, config)
    return _decide_after_tools(agent, state, processed, pre_step_items, new_items, outputs)


def _decide_after_tools(
    agent: Agent,
    state: RunState,
    processed: ProcessedResponse,
    pre_step_items: list[RunItem],
    new_items: list[RunItem],
    outputs: list[RunItem],
) -> SingleStepResult:
    """Upstream: tail of `execute_tools_and_side_effects` (turn_resolution.py:1000-1137)."""
    if outputs and agent.tool_use_behavior == "stop_on_first_tool":
        first = outputs[0].item["output"]
        return SingleStepResult(state.original_input, pre_step_items, new_items, NextStepFinalOutput(first))
    if processed.functions or processed.not_found:
        # Tools ran: the model must see their outputs before anything is final.
        return SingleStepResult(state.original_input, pre_step_items, new_items, NextStepRunAgain())
    text = processed.message_text or ""
    if agent.output_type is not None:
        try:
            final = agent.output_type(text)
        except Exception as exc:
            raise ModelBehaviorError(f"Invalid structured output: {exc}") from exc
        return SingleStepResult(state.original_input, pre_step_items, new_items, NextStepFinalOutput(final))
    return SingleStepResult(state.original_input, pre_step_items, new_items, NextStepFinalOutput(text))


async def execute_handoff(
    agent: Agent,
    state: RunState,
    pre_step_items: list[RunItem],
    new_items: list[RunItem],
    handoffs: list[tuple[Item, Handoff]],
    config: RunConfig,
) -> SingleStepResult:
    """Switch agents; only the first handoff wins (upstream `execute_handoffs`, turn_resolution.py:537)."""
    for extra_call, _ in handoffs[1:]:
        new_items.append(
            RunItem(
                function_call_output(extra_call["call_id"], "Multiple handoffs detected, ignoring this one."),
                agent.name,
            )
        )
    call, chosen = handoffs[0]
    new_items.append(RunItem(function_call_output(call["call_id"], chosen.transfer_message()), agent.name))

    original_input: str | list[Item] = state.original_input
    session_items: list[RunItem] | None = None
    input_filter = chosen.input_filter or config.handoff_input_filter
    nest = chosen.nest_handoff_history if chosen.nest_handoff_history is not None else config.nest_handoff_history
    data = HandoffInputData(tuple(input_to_list(original_input)), tuple(pre_step_items), tuple(new_items))
    if input_filter is not None:
        data = input_filter(data)
        session_items = list(new_items)
        original_input = list(data.input_history)
        pre_step_items, new_items = list(data.pre_handoff_items), list(data.new_items)
    elif nest:
        session_items = list(new_items)
        original_input, pre_step_items, new_items = [_nest_history(data)], [], []
    # Default: no filter and no nesting -> the next agent sees the whole raw transcript.
    return SingleStepResult(original_input, pre_step_items, new_items, NextStepHandoff(chosen.agent), session_items)


def _nest_history(data: HandoffInputData) -> Item:
    """Collapse the transcript into one assistant message (upstream `handoffs/history.py:107`)."""
    replay = list(data.input_history) + [
        i for i in (r.to_input() for r in (*data.pre_handoff_items, *data.new_items)) if i
    ]
    lines = []
    for n, item in enumerate(replay, 1):
        if item.get("type") in (None, "message"):
            lines.append(f"{n}. {item.get('role')}: {text_of(item)}")
        else:
            lines.append(f"{n}. {json.dumps(item, ensure_ascii=False)}")
    body = "\n".join(lines)
    return {
        "role": "assistant",
        "content": "For context, here is the conversation so far between the user and the previous "
        f"agent:\n<CONVERSATION HISTORY>\n{body}\n</CONVERSATION HISTORY>",
    }


async def resolve_interrupted_turn(agent: Agent, state: RunState, config: RunConfig) -> SingleStepResult:
    """Finish the paused turn: run approved calls, answer rejected ones, never re-call the model.

    Upstream: `resolve_interrupted_turn` (`turn_resolution.py:1173`).
    """
    pre_step = state.generated_items[: state.turn_start]
    paused = [i for i in state.generated_items[state.turn_start :] if i.kind != "tool_approval_item"]
    tool_map = {t.name: t for t in agent.tools}
    runs = [ToolRun(i.item, tool_map[i.item["name"]]) for i in state.get_interruptions()]
    outputs, still_pending = await _execute_function_tools(agent, state, runs)
    new_items = paused + outputs
    if still_pending:
        return SingleStepResult(state.original_input, pre_step, new_items + still_pending, NextStepInterruption(still_pending))

    answered = {i.item["call_id"] for i in new_items if i.kind == "function_call_output"}
    handoff_map = {h.tool_name: h for h in agent.get_handoffs()}
    pending_handoffs = [
        (i.item, handoff_map[i.item["name"]])
        for i in paused
        if i.kind == "function_call" and i.item["name"] in handoff_map and i.item["call_id"] not in answered
    ]
    if pending_handoffs:
        return await execute_handoff(agent, state, pre_step, new_items, pending_handoffs, config)
    if outputs and agent.tool_use_behavior == "stop_on_first_tool":
        return SingleStepResult(state.original_input, pre_step, new_items, NextStepFinalOutput(outputs[0].item["output"]))
    return SingleStepResult(state.original_input, pre_step, new_items, NextStepRunAgain())
