"""Agent = declarative configuration. Handoff = a tool that switches the active agent.

Upstream: `Agent` dataclass (`src/agents/agent.py:319`), `Handoff` (`src/agents/handoffs/__init__.py:126`),
`Agent.as_tool` (`agent.py:606-1127`), guardrails (`src/agents/guardrail.py`).

An agent owns no control flow. The runner resolves its instructions, tools and handoffs
again on every turn (`run_single_turn`, `src/agents/run_internal/run_loop.py:2665`).
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from .items import Item
from .tool import FunctionTool, default_tool_error_function

if TYPE_CHECKING:
    from .model import Model
    from .run import HandoffInputData


# ----------------------------------------------------------------------------- guardrails ---


@dataclass
class GuardrailOutput:
    tripwire_triggered: bool
    output_info: Any = None


@dataclass
class InputGuardrail:
    fn: Callable[[Any, Agent, list[Item]], Awaitable[GuardrailOutput]]
    run_in_parallel: bool = True
    """True: race the first model call (cheaper latency, may waste a model call)."""


@dataclass
class OutputGuardrail:
    fn: Callable[[Any, Agent, Any], Awaitable[GuardrailOutput]]


class InputGuardrailTripwireTriggered(Exception):
    pass


class OutputGuardrailTripwireTriggered(Exception):
    pass


# ------------------------------------------------------------------------------- handoffs ---


def _snake(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")


@dataclass
class Handoff:
    tool_name: str
    tool_description: str
    agent: Agent
    input_json_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    input_filter: Callable[[HandoffInputData], HandoffInputData] | None = None
    nest_handoff_history: bool | None = None

    def transfer_message(self) -> str:
        # Upstream `Handoff.get_transfer_message` (`handoffs/__init__.py:210`).
        return json.dumps({"assistant": self.agent.name})


def handoff(
    agent: Agent,
    *,
    tool_name: str | None = None,
    input_filter: Callable[[HandoffInputData], HandoffInputData] | None = None,
    nest_handoff_history: bool | None = None,
) -> Handoff:
    return Handoff(
        tool_name=tool_name or f"transfer_to_{_snake(agent.name)}",
        tool_description=(
            f"Handoff to the {agent.name} agent to handle the request. "
            f"{agent.handoff_description or ''}"
        ).strip(),
        agent=agent,
        input_filter=input_filter,
        nest_handoff_history=nest_handoff_history,
    )


# ---------------------------------------------------------------------------------- agent ---

Instructions = str | Callable[[Any, "Agent"], str | Awaitable[str]] | None


@dataclass
class Agent:
    name: str
    instructions: Instructions = None
    tools: list[FunctionTool] = field(default_factory=list)
    handoffs: list[Agent | Handoff] = field(default_factory=list)
    model: Model | None = None
    model_settings: dict[str, Any] = field(default_factory=dict)
    handoff_description: str | None = None
    output_type: Callable[[str], Any] | None = None
    """Parser/validator for structured final output; raising means invalid output."""
    tool_use_behavior: Literal["run_llm_again", "stop_on_first_tool"] = "run_llm_again"
    reset_tool_choice: bool = True
    input_guardrails: list[InputGuardrail] = field(default_factory=list)
    output_guardrails: list[OutputGuardrail] = field(default_factory=list)

    async def get_system_prompt(self, ctx: Any) -> str | None:
        if self.instructions is None or isinstance(self.instructions, str):
            return self.instructions
        result = self.instructions(ctx, self)
        return await result if inspect.isawaitable(result) else result

    def get_handoffs(self) -> list[Handoff]:
        return [h if isinstance(h, Handoff) else handoff(h) for h in self.handoffs]

    def clone(self, **changes: Any) -> Agent:
        return replace(self, **changes)

    def as_tool(self, tool_name: str, tool_description: str, *, max_turns: int = 10) -> FunctionTool:
        """Expose this agent as a tool. The parent keeps control (unlike a handoff).

        The nested run starts from *only* the generated `input` argument: no parent history
        (upstream `_run_agent_impl`, `agent.py:721-1100`). It shares the application context
        object but uses a fresh run wrapper, so approvals do not leak across runs
        (`agent.py:760-772`). The parent receives the final output as the tool result.
        """

        async def run_nested(ctx: Any, raw_arguments: str) -> Any:
            from .run import Runner

            args = json.loads(raw_arguments or "{}")
            result = await Runner.run(
                self, args["input"], context=ctx.context, max_turns=max_turns, run_config=ctx.run_config
            )
            return result.final_output

        return FunctionTool(
            name=tool_name,
            description=tool_description,
            params_json_schema={
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            },
            on_invoke_tool=run_nested,
            failure_error_function=default_tool_error_function,
        )
