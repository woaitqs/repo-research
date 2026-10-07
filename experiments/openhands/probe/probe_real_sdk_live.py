"""Live probe: the real OpenHands SDK (software-agent-sdk v1.53.0) driven by a real
OpenAI-compatible model (Volcano Engine Ark Coding Plan) through LiteLLM.

Reads ARK_API_KEY / ARK_BASE_URL / ARK_MODEL from the environment; prints a JSON
report of what the real agent loop did and what it persisted.

    OPENHANDS_SUPPRESS_BANNER=1 uv run --frozen python probe_real_sdk_live.py <out_dir>
"""

import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Condensation,
    MessageEvent,
    ObservationEvent,
)
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def main(out: Path) -> dict:
    model = os.environ.get("ARK_MODEL", "doubao-seed-2-1-pro-260915")
    llm = LLM(
        model=f"openai/{model}",
        base_url=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"),
        api_key=SecretStr(os.environ["ARK_API_KEY"]),
        usage_id="agent",
        num_retries=2,
    )
    condenser_llm = llm.model_copy(update={"usage_id": "condenser"})
    agent = Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
        condenser=LLMSummarizingCondenser(
            llm=condenser_llm, max_size=int(os.environ.get("PROBE_MAX_SIZE", "12")), keep_first=2
        ),
    )
    workspace = out / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    conv = Conversation(
        agent=agent,
        workspace=str(workspace),
        persistence_dir=str(out / "conversations"),
        visualizer=None,
        max_iteration_per_run=30,
    )
    conv.send_message(
        "Create fib.py that prints the first 10 Fibonacci numbers, one per line, and run it. "
        "Then create test_fib.py with a plain-assert test that runs fib.py via subprocess and "
        "checks the last line is 34, run it with python3, and finish with a one-sentence report."
    )
    conv.run()

    state = conv.state
    events = list(state.events)
    tool_calls = Counter(e.tool_name for e in events if isinstance(e, ActionEvent))
    system_msg = state.view.events[0].to_llm_message()
    sys_blocks = [len(c.text) for c in system_msg.content]
    finish = next(
        (e for e in reversed(events) if isinstance(e, ActionEvent) and e.tool_name == "finish"),
        None,
    )
    return {
        "model": model,
        "execution_status": state.execution_status.value,
        "event_count": len(events),
        "event_type_counts": dict(Counter(type(e).__name__ for e in events)),
        "tool_calls": dict(tool_calls),
        "agent_errors": [e.error[:200] for e in events if isinstance(e, AgentErrorEvent)],
        "condensations": [
            {"forgotten": len(e.forgotten_event_ids), "offset": e.summary_offset, "summary_head": (e.summary or "")[:200]}
            for e in events if isinstance(e, Condensation)
        ],
        "final_view_len": len(state.view),
        "system_prompt_blocks_chars": sys_blocks,
        "finish_message": finish.action.message if finish is not None and finish.action is not None else None,
        "agent_message": next(
            (e.llm_message.content[0].text for e in reversed(events)
             if isinstance(e, MessageEvent) and e.source == "agent" and e.llm_message.content), None),
        "observation_count": sum(isinstance(e, ObservationEvent) for e in events),
        "workspace_files": sorted(p.name for p in workspace.iterdir()),
        "fib_output": (workspace / "fib.py").exists(),
        "accumulated_cost": state.stats.get_combined_metrics().accumulated_cost,
        "prompt_tokens": state.stats.get_combined_metrics().accumulated_token_usage.prompt_tokens
        if state.stats.get_combined_metrics().accumulated_token_usage else None,
    }


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    print(json.dumps(main(out), indent=2, ensure_ascii=False))
