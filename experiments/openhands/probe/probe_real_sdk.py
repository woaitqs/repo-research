"""Runtime probe of the real OpenHands SDK (software-agent-sdk v1.53.0).

Drives a real LocalConversation with a scripted TestLLM (no network), the real
TerminalTool / FileEditorTool, and an LLMSummarizingCondenser with a tiny
max_size so condensation actually fires. Then inspects what was persisted.

Usage (from a software-agent-sdk checkout at v1.53.0):
    OPENHANDS_SUPPRESS_BANNER=1 uv run --frozen python probe_real_sdk.py <out_dir>
"""

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Condensation,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def call(i: int, name: str, args: dict | str) -> Message:
    arguments = args if isinstance(args, str) else json.dumps(args)
    return Message(
        role="assistant",
        content=[TextContent(text=f"step {i}: calling {name}")],
        tool_calls=[
            MessageToolCall(
                id=f"call_{i}", name=name, arguments=arguments, origin="completion"
            )
        ],
    )


def main(out_dir: Path) -> dict:
    workspace = out_dir / "workspace"
    persistence = out_dir / "conversations"
    workspace.mkdir(parents=True, exist_ok=True)

    agent_llm = TestLLM.from_messages(
        [
            call(1, "terminal", {"command": "echo hello > a.txt && ls"}),
            call(2, "file_editor", {"command": "create", "path": str(workspace / "notes.md"), "file_text": "# notes\n"}),
            call(3, "terminal", {"command": "seq 1 200000"}),
            call(4, "does_not_exist", {"x": 1}),
            call(5, "terminal", '{"command": "ls" '),  # malformed JSON
            call(6, "terminal", {"command": "cat a.txt"}),
            call(7, "finish", {"message": "done"}),
        ],
        usage_id="agent",
    )
    condenser_llm = TestLLM.from_messages(
        [
            Message(role="assistant", content=[TextContent(text="SUMMARY#1: wrote a.txt, notes.md, ran seq")]),
            Message(role="assistant", content=[TextContent(text="SUMMARY#2: rolled up summary")]),
            Message(role="assistant", content=[TextContent(text="SUMMARY#3: rolled up again")]),
        ],
        usage_id="condenser",
    )
    agent = Agent(
        llm=agent_llm,
        tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
        condenser=LLMSummarizingCondenser(llm=condenser_llm, max_size=10, keep_first=2),
    )
    conv = Conversation(
        agent=agent,
        workspace=str(workspace),
        persistence_dir=str(persistence),
        visualizer=None,
    )
    conv.send_message("Create a.txt and notes.md, then report.")
    conv.run()

    state = conv.state
    events = list(state.events)
    types = [type(e).__name__ for e in events]

    seq_obs = next(
        e for e in events
        if isinstance(e, ObservationEvent) and e.tool_call_id == "call_3"
    )
    seq_text = "".join(
        c.text for c in seq_obs.observation.to_llm_content if isinstance(c, TextContent)
    )

    view_types = [type(e).__name__ for e in state.view.events]
    conv_dir = next(persistence.iterdir())
    files = sorted(p.relative_to(conv_dir).as_posix() for p in conv_dir.rglob("*") if p.is_file())

    report = {
        "execution_status": state.execution_status.value,
        "event_type_counts": dict(Counter(types)),
        "event_sequence": types,
        "condensations": [
            {"forgotten": len(e.forgotten_event_ids), "summary": e.summary, "offset": e.summary_offset}
            for e in events if isinstance(e, Condensation)
        ],
        "agent_errors": [e.error[:160] for e in events if isinstance(e, AgentErrorEvent)],
        "seq_observation_chars_sent_to_llm": len(seq_text),
        "seq_observation_head": seq_text[:120],
        "seq_observation_tail": seq_text[-200:],
        "final_view_types": view_types,
        "system_prompt_first_in_view": isinstance(state.view.events[0], SystemPromptEvent),
        "persisted_files_sample": files[:6] + ["..."] + files[-3:],
        "persisted_file_count": len(files),
        "agent_llm_calls": agent_llm._call_count,
        "condenser_llm_calls": condenser_llm._call_count,
        "workspace_files": sorted(p.name for p in workspace.iterdir()),
        "last_agent_message": next(
            (e.llm_message.content[0].text for e in reversed(events)
             if isinstance(e, MessageEvent) and e.source == "agent"), None),
        "action_events": sum(isinstance(e, ActionEvent) for e in events),
    }
    return report


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    print(json.dumps(main(out), indent=2))
