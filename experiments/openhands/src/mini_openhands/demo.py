"""End-to-end demo.

    python -m mini_openhands.demo            # scripted model, fully offline
    python -m mini_openhands.demo --live     # real OpenAI-compatible model (Ark)

The scripted run exercises: tool calls, an invalid call that becomes an
AgentErrorEvent, a large output that is truncated + offloaded, a condensation,
finish, then a *second process-like* Conversation that resumes from disk.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from . import (
    Agent,
    Conversation,
    LLMResponse,
    OpenAICompatibleLLM,
    ScriptedLLM,
    SummarizingCondenser,
    ToolSpec,
    tool_call,
)
from .events import Event


def describe(event: Event) -> str:
    name = type(event).__name__
    detail = ""
    for attr in ("text", "tool_name", "error", "summary", "code"):
        value = getattr(event, attr, None)
        if value:
            detail = f"{attr}={str(value)[:70]!r}"
            break
    if name == "ObservationEvent":
        detail += f" ({len(event.content)} chars)"
    if name == "Condensation":
        detail += f" forgot={len(event.forgotten_event_ids)} offset={event.summary_offset}"
    return f"{name:<24} {event.source:<12} {detail}"


def printer(event: Event) -> None:
    print("  +", describe(event))


def scripted(root: Path) -> None:
    workspace, store = root / "workspace", root / "conversations"
    agent_llm = ScriptedLLM(
        [
            LLMResponse(text="I'll write the script first.", tool_calls=[tool_call("file_editor", {"command": "create", "path": "fib.py", "file_text": "a, b = 0, 1\nfor _ in range(10):\n    print(a)\n    a, b = b, a + b\n"})]),
            LLMResponse(tool_calls=[tool_call("terminal", {"command": "python3 fib.py"})]),
            LLMResponse(tool_calls=[tool_call("terminal", '{"command": "ls" ')]),  # malformed JSON
            LLMResponse(tool_calls=[tool_call("terminal", {"command": "seq 1 50000"})]),
            LLMResponse(tool_calls=[tool_call("terminal", {"command": "wc -l fib.py"})]),
            LLMResponse(tool_calls=[tool_call("finish", {"message": "fib.py prints the first 10 Fibonacci numbers."})]),
        ]
    )
    summarizer = ScriptedLLM([LLMResponse(text="USER_CONTEXT: write fib.py. COMPLETED: created + ran fib.py, printed seq. PENDING: report.")])
    agent = Agent(
        llm=agent_llm,
        tools=[ToolSpec("terminal"), ToolSpec("file_editor")],
        condenser=SummarizingCondenser(summarizer, max_size=8, keep_first=2),
    )
    conv = Conversation(agent, workspace, persistence_dir=store, conversation_id="demo", callbacks=[printer])
    print("== run 1: new conversation")
    conv.send_message("Write fib.py that prints 10 Fibonacci numbers, run it, then report.")
    conv.run()
    print(f"status={conv.state.status.value}  events={len(conv.state.events)}  view={len(conv.state.view)}")
    print("view sent to the LLM next:", [m["role"] for m in conv.state.view.to_messages()])

    print("\n== run 2: a new Conversation object resumes from disk")
    agent2 = Agent(
        llm=ScriptedLLM([LLMResponse(tool_calls=[tool_call("terminal", {"command": "python3 fib.py | tail -1"})]), LLMResponse(text="The 10th number is 34.")]),
        tools=[ToolSpec("terminal"), ToolSpec("file_editor")],
    )
    resumed = Conversation(agent2, workspace, persistence_dir=store, conversation_id="demo", callbacks=[printer])
    print(f"resumed with {len(resumed.state.events)} events, status={resumed.state.status.value}")
    resumed.send_message("What is the last number it prints?")
    resumed.run()
    print(f"status={resumed.state.status.value}  events={len(resumed.state.events)}")

    files = sorted(p.relative_to(store / "demo").as_posix() for p in (store / "demo").rglob("*") if p.is_file())
    print(f"\npersisted files ({len(files)}):", files[:3], "...", files[-2:])


def live(root: Path) -> None:
    llm = OpenAICompatibleLLM.from_env()
    agent = Agent(
        llm=llm,
        tools=[ToolSpec("terminal"), ToolSpec("file_editor")],
        # deliberately tiny so a real model run exercises summarization
        condenser=SummarizingCondenser(llm, max_size=8, keep_first=2),
    )
    conv = Conversation(agent, root / "workspace", persistence_dir=root / "conversations", callbacks=[printer], max_iterations=25)
    conv.send_message(
        "Create fib.py that prints the first 10 Fibonacci numbers, one per line, and run it. "
        "Then create test_fib.py with a plain-assert test that runs fib.py via subprocess and "
        "checks the last line is 34, run it with python3, and finally call the finish tool "
        "with a one-sentence report."
    )
    conv.run()
    from .events import Condensation, ObservationEvent

    events = list(conv.state.events)
    print(f"model={llm.model} status={conv.state.status.value} events={len(events)} view={len(conv.state.view)}")
    for c in (e for e in events if isinstance(e, Condensation)):
        print(f"condensation: forgot={len(c.forgotten_event_ids)} summary={c.summary[:160]!r}")
    finish = [e for e in events if isinstance(e, ObservationEvent) and e.tool_name == "finish"]
    if finish:
        print("finish message:", finish[-1].content[:200])
    ws = root / "workspace"
    print("workspace files:", sorted(p.name for p in ws.iterdir()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use ARK_API_KEY / ARK_BASE_URL / ARK_MODEL")
    parser.add_argument("--keep", action="store_true", help="keep the run directory")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="mini-openhands-"))
    try:
        (live if args.live else scripted)(root)
    finally:
        if args.keep:
            print("run directory:", root)
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
