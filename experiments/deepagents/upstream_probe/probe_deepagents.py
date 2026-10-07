"""Runtime probe: drive the REAL deepagents SDK with a scripted fake model.

Verifies architectural claims made from reading source, without any network/API key.
Run with the venv that has `deepagents` installed (editable, commit 16e84d9).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

import deepagents.graph as dg
from deepagents import create_deep_agent
from deepagents.backends.utils import create_file_data

RESULTS: dict[str, Any] = {}


class ScriptedModel(BaseChatModel):
    """Fake chat model: records every request and answers via a script callback."""

    script: Callable[[list[BaseMessage], list[str], int], AIMessage]
    calls: list[dict[str, Any]] = Field(default_factory=list)
    label: str = "model"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any):  # noqa: ANN201
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN202
        names = [t["function"]["name"] for t in kwargs.get("tools", [])]
        self.calls.append({"messages": list(messages), "tools": names})
        msg = self.script(list(messages), names, len(self.calls))
        return ChatResult(generations=[ChatGeneration(message=msg)])


def tc(name: str, args: dict, cid: str) -> dict:
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


def is_summary_request(messages: list[BaseMessage]) -> bool:
    return len(messages) == 1 and isinstance(messages[0], HumanMessage) and "<messages>" in str(messages[0].content)


# ---------------------------------------------------------------------------
# P1: capture the assembled middleware stack by spying on create_agent
# ---------------------------------------------------------------------------
captured: dict[str, Any] = {}
_real_create_agent = dg.create_agent


def _spy_create_agent(model, **kwargs):  # noqa: ANN001, ANN202
    captured["middleware"] = [m.name for m in kwargs["middleware"]]
    captured["system_prompt"] = kwargs["system_prompt"]
    return _real_create_agent(model, **kwargs)


dg.create_agent = _spy_create_agent


# ---------------------------------------------------------------------------
# Scenario A: tool filtering + large-result offload + sub-agent isolation
# ---------------------------------------------------------------------------
@tool
def big_search(query: str) -> str:
    """Return a very large search dump."""
    return "\n".join(f"line {i}: result for {query} " + "x" * 80 for i in range(1500))


def main_script_a(messages, tools, n):  # noqa: ANN001, ANN201
    if n == 1:
        return AIMessage(content="", tool_calls=[tc("big_search", {"query": "deepagents"}, "call_big_1")])
    if n == 2:
        return AIMessage(
            content="",
            tool_calls=[tc("task", {"description": "Write a short report to /report.md", "subagent_type": "writer"}, "call_task_1")],
        )
    return AIMessage(content="All done.")


def writer_script(messages, tools, n):  # noqa: ANN001, ANN201
    if n == 1:
        return AIMessage(content="", tool_calls=[tc("write_file", {"file_path": "/report.md", "content": "# Report\nhello"}, "call_w1")])
    return AIMessage(content="Report written to /report.md")


main_a = ScriptedModel(script=main_script_a, label="main")
writer = ScriptedModel(script=writer_script, label="writer")
agent_a = create_deep_agent(
    model=main_a,
    tools=[big_search],
    subagents=[{"name": "writer", "description": "Writes reports", "system_prompt": "You write reports.", "model": writer}],
)
RESULTS["P1_middleware_stack"] = captured["middleware"]
res_a = agent_a.invoke({"messages": [HumanMessage("research deepagents and write a report")]})

RESULTS["P2_tools_visible_to_main_model"] = main_a.calls[0]["tools"]
RESULTS["P2_execute_hidden_with_StateBackend"] = "execute" not in main_a.calls[0]["tools"]

offload_keys = [k for k in res_a["files"] if k.startswith("/large_tool_results/")]
tool_msg_seen = next(m for m in main_a.calls[1]["messages"] if isinstance(m, ToolMessage))
RESULTS["P3_offloaded_files"] = offload_keys
RESULTS["P3_offloaded_size_chars"] = len("\n".join(res_a["files"][offload_keys[0]]["content"])) if offload_keys and isinstance(res_a["files"][offload_keys[0]]["content"], list) else len(res_a["files"][offload_keys[0]]["content"]) if offload_keys else 0
RESULTS["P3_model_sees_stub_chars"] = len(str(tool_msg_seen.content))
RESULTS["P3_stub_head"] = str(tool_msg_seen.content).splitlines()[0][:120]

sub_first = writer.calls[0]["messages"]
RESULTS["P4_subagent_first_call_message_types"] = [type(m).__name__ for m in sub_first]
RESULTS["P4_subagent_human_content"] = [str(m.content) for m in sub_first if isinstance(m, HumanMessage)]
RESULTS["P4_subagent_tools"] = writer.calls[0]["tools"]
RESULTS["P4_report_file_in_parent_state"] = "/report.md" in res_a["files"]
task_result = next(m for m in main_a.calls[2]["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "call_task_1")
RESULTS["P4_parent_sees_only_final_text"] = str(task_result.content)
RESULTS["P4_parent_message_count"] = len(res_a["messages"])

# ---------------------------------------------------------------------------
# Scenario B: summarization is non-destructive (event + effective view)
# ---------------------------------------------------------------------------
@tool
def read_chunk(i: int) -> str:
    """Return a medium-size chunk (below eviction threshold)."""
    return f"chunk {i}: " + ("lorem ipsum dolor sit amet " * 160)


summary_seen_at: list[int] = []


def main_script_b(messages, tools, n):  # noqa: ANN001, ANN201
    if is_summary_request(messages):
        return AIMessage(content="SUMMARY: read chunks 0..k, nothing else notable.")
    non_system = [m for m in messages if not isinstance(m, SystemMessage)]
    if non_system and isinstance(non_system[0], HumanMessage) and "has been summarized" in str(non_system[0].content):
        summary_seen_at.append(n)
        return AIMessage(content="Finished after compaction.")
    k = sum(isinstance(m, ToolMessage) for m in messages)
    return AIMessage(content="", tool_calls=[tc("read_chunk", {"i": k}, f"call_chunk_{k}")])


main_b = ScriptedModel(script=main_script_b, label="main_b", profile={"max_input_tokens": 30000})
saver_b = InMemorySaver()
agent_b = create_deep_agent(model=main_b, tools=[read_chunk], checkpointer=saver_b)
cfg_b = {"configurable": {"thread_id": "b"}, "recursion_limit": 400}
res_b = agent_b.invoke({"messages": [HumanMessage("read chunks until done")]}, cfg_b)
state_b = agent_b.get_state(cfg_b).values
event = state_b.get("_summarization_event")
RESULTS["P5_summary_seen_at_model_call"] = summary_seen_at
RESULTS["P5_state_message_count"] = len(state_b["messages"])
RESULTS["P5_event_cutoff_index"] = event["cutoff_index"] if event else None
RESULTS["P5_event_file_path"] = event["file_path"] if event else None
RESULTS["P5_history_file_in_state"] = any(k.startswith("/conversation_history/") for k in state_b.get("files", {}))
last_call_msgs = main_b.calls[-1]["messages"]
RESULTS["P5_last_model_call_message_count"] = len(last_call_msgs)
RESULTS["P5_first_state_message_still_original"] = str(state_b["messages"][0].content)

# ---------------------------------------------------------------------------
# Scenario C: memory loaded once per thread (staleness check)
# ---------------------------------------------------------------------------
def main_script_c(messages, tools, n):  # noqa: ANN001, ANN201
    if n == 1:
        return AIMessage(
            content="",
            tool_calls=[tc("edit_file", {"file_path": "/AGENTS.md", "old_string": "likes python", "new_string": "likes rust"}, "call_e1")],
        )
    return AIMessage(content="ok")


main_c = ScriptedModel(script=main_script_c, label="main_c")
agent_c = create_deep_agent(model=main_c, memory=["/AGENTS.md"], checkpointer=InMemorySaver())
cfg_c = {"configurable": {"thread_id": "c"}}
agent_c.invoke({"messages": [HumanMessage("remember I like rust")], "files": {"/AGENTS.md": create_file_data("user likes python")}}, cfg_c)
agent_c.invoke({"messages": [HumanMessage("what do I like?")]}, cfg_c)
sys_turn2 = str(main_c.calls[-1]["messages"][0].content)
file_now = agent_c.get_state(cfg_c).values["files"]["/AGENTS.md"]["content"]
RESULTS["P6_file_content_after_edit"] = file_now if isinstance(file_now, str) else "\n".join(file_now)
RESULTS["P6_turn2_system_prompt_has_python"] = "likes python" in sys_turn2
RESULTS["P6_turn2_system_prompt_has_rust"] = "likes rust" in sys_turn2

# ---------------------------------------------------------------------------
# Scenario D: a delete inside an isolated sub-agent vs. the parent's `files`
# ---------------------------------------------------------------------------
def parent_script_d(messages, tools, n):  # noqa: ANN001, ANN201
    if n == 1:
        return AIMessage(content="", tool_calls=[tc("task", {"description": "delete /old.md", "subagent_type": "cleaner"}, "t1")])
    return AIMessage(content="done")


def cleaner_script(messages, tools, n):  # noqa: ANN001, ANN201
    if n == 1:
        return AIMessage(content="", tool_calls=[tc("delete", {"file_path": "/old.md"}, "d1")])
    return AIMessage(content="deleted /old.md")


cleaner = ScriptedModel(script=cleaner_script)
agent_d = create_deep_agent(
    model=ScriptedModel(script=parent_script_d),
    subagents=[{"name": "cleaner", "description": "deletes files", "system_prompt": "x", "model": cleaner}],
)
res_d = agent_d.invoke({"messages": [HumanMessage("clean")], "files": {"/old.md": create_file_data("stale"), "/keep.md": create_file_data("k")}})
RESULTS["P7_subagent_delete_tool_result"] = [str(m.content) for m in cleaner.calls[1]["messages"] if isinstance(m, ToolMessage)]
RESULTS["P7_parent_files_after"] = sorted(res_d["files"])

json.dump(RESULTS, sys.stdout, indent=2, default=str)
print()
