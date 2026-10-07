"""Sub-agents: a tool that launches another harness and returns only its report.

Upstream (`src/agent/subagents/manager.ts:180-268,415-460`) spawns a separate
headless `letta` process per sub-agent:
`--new-agent --system <type> --output-format stream-json --permission-mode unrestricted`,
prompt on stdin, `LETTA_SUBAGENT_DEPTH` = parent + 1, `MAX_SUBAGENT_DEPTH = 2`.

Two context modes:
* fresh (`general-purpose`): new agent, new conversation, *no memory*; the child
  sees only its system prompt and the prompt it was handed;
* `fork`: the backend copies the parent conversation (`forkConversation`), the
  child continues as the parent agent with a "you are NOT the primary agent"
  reminder.
Either way only the final report text flows back to the parent.

minilc runs the child harness in-process for testability, but keeps the same
contract: separate conversation, depth counter, tool list without `Agent` at the
leaf, report-only return.
"""

from __future__ import annotations

import os
from typing import Any

from .tools import Tool, ToolContext

MAX_SUBAGENT_DEPTH = 2
FORK_REMINDER = ("<system-reminder>You are NOT the primary agent. You are a forked subagent; "
                 "your sole task is the user message below.</system-reminder>\n")


def agent_tool(make_child_harness, depth: int = 0) -> Tool:
    """`make_child_harness(subagent_type, depth, conversation_id) -> (Harness, conversation_id)`."""

    def run(args: dict[str, Any], ctx: ToolContext) -> Any:
        if depth >= MAX_SUBAGENT_DEPTH:
            return {"status": "error", "content": "Subagent depth limit reached"}
        subagent_type = args.get("subagent_type", "general-purpose")
        child, conv_id = make_child_harness(subagent_type, depth + 1, ctx.extras.get("conversation_id"))
        prompt = args["prompt"]
        if subagent_type == "fork":
            prompt = FORK_REMINDER + prompt
        result = child.run(conv_id, prompt)
        return result.text or "(subagent returned no text)"

    schema = {"type": "object", "properties": {"prompt": {"type": "string"},
                                               "subagent_type": {"type": "string", "enum": ["general-purpose", "fork"]}},
              "required": ["prompt"]}
    return Tool("Agent", "Launch a subagent with a self-contained task; only its final report returns.", schema, run)


def child_factory(backend, parent_agent: dict[str, Any], file_tools, cwd: str, overflow_dir: str):
    """Build children the way upstream does: fresh agents are new, memory-less
    agents; forks reuse the parent agent on a copied conversation. A child at
    depth d gets the `Agent` tool only while d < MAX_SUBAGENT_DEPTH
    (subagent-depth.ts:11-43)."""
    from .harness import Harness
    from .permissions import PermissionPolicy
    from .tools import ToolRegistry

    def make(subagent_type: str, depth: int, parent_conversation_id: str | None):
        tools = list(file_tools.tools.values())
        if depth < MAX_SUBAGENT_DEPTH:
            tools.append(agent_tool(make, depth))
        if subagent_type == "fork" and parent_conversation_id:
            agent = parent_agent
            conv = backend.create_conversation(agent["id"], fork_of=parent_conversation_id)
        else:
            agent = backend.create_agent(f"{subagent_type}-child", "You are a subagent. Complete the task and "
                                         "reply with a concise final report.\n{CORE_MEMORY}", memfs=False)
            conv = backend.create_conversation(agent["id"])
        ctx = ToolContext(cwd=cwd, overflow_dir=os.path.join(overflow_dir, f"child-{depth}"))
        # children: unrestricted mode, no shared reminders (catalog has no subagent mode)
        return Harness(backend, agent, ToolRegistry(tools, file_tools.pre_tool_hooks), ctx,
                       PermissionPolicy(mode="unrestricted", cwd=cwd), reminders=False), conv

    return make
