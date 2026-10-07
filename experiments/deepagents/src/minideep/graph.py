"""`create_deep_agent`: assemble an opinionated middleware stack around a plain loop.

Mirrors deepagents/graph.py::create_deep_agent. The harness adds no new runtime; it
decides WHICH middleware run, in WHAT order, and hands everything to the loop
(upstream: LangChain `create_agent`). Main-agent order (outermost first):

    Filesystem -> SubAgent -> Summarization -> PatchToolCalls -> [caller middleware]
    -> Skills -> Memory

Memory sits at the tail so that the stable prefix of the system prompt (caller
prompt, skills) comes first — upstream places prompt caching right before memory for
the same reason. Sub-agents get Filesystem -> Summarization -> PatchToolCalls but no
SubAgent middleware, so they cannot delegate further.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from minideep.backends import BackendProtocol, StateBackend
from minideep.filesystem import FilesystemMiddleware
from minideep.loop import Agent, Tracer
from minideep.memory import MemoryMiddleware, SkillsMiddleware
from minideep.messages import AIMessage, ReplaceMessages, ToolMessage
from minideep.middleware import Middleware
from minideep.model import ChatModel
from minideep.subagents import SubAgentMiddleware
from minideep.summarization import SummarizationMiddleware
from minideep.tools import Tool

GENERAL_PURPOSE_PROMPT = (
    "Complete the delegated task with the tools available. The calling agent only sees your final "
    "message, so make it the complete answer."
)


class PatchToolCallsMiddleware(Middleware):
    """Repair history: every tool call gets a result, even if a run was interrupted.

    Mirrors deepagents/middleware/patch_tool_calls.py — providers reject transcripts
    where an AIMessage tool call has no matching ToolMessage.
    """

    def before_agent(self, state: dict[str, Any]) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
        dangling = [tc for m in messages if isinstance(m, AIMessage) for tc in m.tool_calls if tc.id not in answered]
        if not dangling:
            return None
        patched = []
        for m in messages:
            patched.append(m)
            if isinstance(m, AIMessage):
                patched += [
                    ToolMessage(f"Tool call {tc.name} with id {tc.id} did not complete.", tool_call_id=tc.id, name=tc.name, status="error")
                    for tc in m.tool_calls
                    if tc.id not in answered
                ]
        return {"messages": ReplaceMessages(patched)}


@dataclass
class SubAgentSpec:
    name: str
    description: str
    system_prompt: str = ""
    model: ChatModel | None = None
    tools: Sequence[Tool] | None = None
    middleware: Sequence[Middleware] = field(default_factory=tuple)


def _core_stack(model: ChatModel, backend: BackendProtocol, evict: int | None, summarization: dict[str, Any]) -> list[Middleware]:
    return [
        FilesystemMiddleware(backend, tool_token_limit_before_evict=evict),
        SummarizationMiddleware(model, backend, **summarization),
        PatchToolCallsMiddleware(),
    ]


def create_deep_agent(
    model: ChatModel,
    tools: Sequence[Tool] = (),
    *,
    system_prompt: str = "",
    subagents: Sequence[SubAgentSpec] = (),
    memory: Sequence[str] | None = None,
    skills: Sequence[str] | None = None,
    backend: BackendProtocol | None = None,
    middleware: Sequence[Middleware] = (),
    tool_token_limit_before_evict: int | None = 20_000,
    summarization_trigger_tokens: int | None = None,
    summarization_keep_messages: int = 6,
    trim_tokens_to_summarize: int | None = None,
    tracer: Tracer | None = None,
) -> Agent:
    backend = backend or StateBackend()
    summarization = {
        "trigger_tokens": summarization_trigger_tokens,
        "keep_messages": summarization_keep_messages,
        "trim_tokens_to_summarize": trim_tokens_to_summarize,
    }

    specs = list(subagents)
    if not any(s.name == "general-purpose" for s in specs):  # auto-added unless overridden
        specs.insert(0, SubAgentSpec("general-purpose", "General agent with the same tools as the main agent.", GENERAL_PURPOSE_PROMPT))
    compiled = []
    for spec in specs:
        sub_model = spec.model or model
        sub_mw = _core_stack(sub_model, backend, tool_token_limit_before_evict, summarization) + list(spec.middleware)
        if spec.name == "general-purpose" and skills is not None:
            sub_mw.append(SkillsMiddleware(backend, skills))
        sub_tools = list(spec.tools) if spec.tools is not None else list(tools)  # inherit unless declared
        agent = Agent(sub_model, system_prompt=spec.system_prompt, tools=sub_tools, middleware=sub_mw, name=spec.name, tracer=tracer)
        compiled.append((spec.name, spec.description, agent))

    core = _core_stack(model, backend, tool_token_limit_before_evict, summarization)
    stack: list[Middleware] = [core[0], SubAgentMiddleware(compiled), *core[1:], *middleware]
    if skills is not None:
        stack.append(SkillsMiddleware(backend, skills))
    if memory is not None:
        stack.append(MemoryMiddleware(backend, memory))
    return Agent(model, system_prompt=system_prompt, tools=tools, middleware=stack, name="main", tracer=tracer)
