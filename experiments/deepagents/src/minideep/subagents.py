"""SubAgentMiddleware: delegation as a tool call with context isolation.

Mirrors deepagents/middleware/subagents.py::_build_task_tool:

- The parent sees ONE tool, `task(description, subagent_type)`, whose description lists
  the available sub-agents.
- Isolated mode (default): the child starts with `messages=[HumanMessage(description)]`
  and a copy of the parent's *non-message, non-private* state (so `files` is shared).
- The child's whole transcript stays inside the child. The parent receives only
  the child's final text as a ToolMessage, plus the child's non-excluded state keys
  (e.g. `files`) merged back through the normal reducers.
- Children get no `task` tool themselves, so delegation cannot recurse.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from minideep.loop import Agent, final_text
from minideep.messages import Command, HumanMessage, ToolMessage
from minideep.middleware import Middleware
from minideep.tools import Tool, ToolRuntime

# Upstream `_EXCLUDED_STATE_KEYS` plus "private" keys (PrivateStateAttr upstream;
# here: any key starting with "_" and the loaded memory/skills caches).
EXCLUDED_STATE_KEYS = frozenset({"messages", "todos", "structured_response", "skills_metadata", "memory_contents"})


def _is_shared(key: str) -> bool:
    return key not in EXCLUDED_STATE_KEYS and not key.startswith("_")


class SubAgentMiddleware(Middleware):
    def __init__(self, subagents: Sequence[tuple[str, str, Agent]]) -> None:
        """`subagents` is a list of `(name, description, compiled_agent)`."""
        self.agents = {name: agent for name, _, agent in subagents}
        listing = "\n".join(f"- {name}: {desc}" for name, desc, _ in subagents)

        def task(description: str, subagent_type: str, runtime: ToolRuntime) -> str | Command:
            if subagent_type not in self.agents:
                allowed = ", ".join(f"`{n}`" for n in self.agents)
                return f"We cannot invoke subagent {subagent_type} because it does not exist, the only allowed types are {allowed}"
            child_state: dict[str, Any] = {k: v for k, v in runtime.state.items() if _is_shared(k)}
            child_state["messages"] = [HumanMessage(description)]
            result = self.agents[subagent_type].invoke(child_state)
            update = {k: v for k, v in result.items() if _is_shared(k)}
            update["messages"] = [ToolMessage(final_text(result), tool_call_id=runtime.tool_call_id, name="task")]
            return Command(update=update)

        self.tools = [
            Tool(
                name="task",
                description=(
                    "Launch an ephemeral subagent for a complex, multi-step task. It sees only the "
                    "description you give it and returns a single final report.\n\nAvailable agent types:\n" + listing
                ),
                func=task,
            )
        ]
