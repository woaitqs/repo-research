"""Narrated end-to-end run of the minideep harness (no network, deterministic).

Scenario: a "coding agent" triages a failing test suite in a scratch workspace.
It exercises every architectural piece and then checks the invariants:

  execute -> huge output OFFLOADED -> grep the offloaded file -> read a SKILL on demand
  -> delegate to an ISOLATED sub-agent -> page through a log until SUMMARIZATION fires
  -> persist a preference to MEMORY (routed to state) -> final answer
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from minideep import (
    AIMessage,
    Command,
    CompositeBackend,
    HumanMessage,
    ScriptedModel,
    ShellBackend,
    StateBackend,
    SubAgentSpec,
    ToolCall,
    ToolMessage,
    create_deep_agent,
    final_text,
    is_summary_request,
)
from minideep.summarization import EVENT_KEY

BIG_TEST_RUN = "python3 -c \"[print(f'tests/test_{i}.py::test_case ' + ('FAILED AssertionError: ids off by one' if i == 1234 else 'PASSED')) for i in range(3000)]\""


def _plan(steps: list[AIMessage]):
    """Script that replays planned actions in order (and answers summary requests)."""
    queue = list(steps)

    def script(messages: list[Any], tools: list[str], n: int) -> AIMessage:
        if is_summary_request(messages):
            return AIMessage("Ran the suite (3000 tests, 1 failure: test_1234, output offloaded); read the test-triage skill; researcher wrote /notes/findings.md; paged CI log.")
        return queue.pop(0)

    return script


def call(name: str, cid: str, **args: Any) -> AIMessage:
    return AIMessage("", tool_calls=[ToolCall(name, args, cid)])


def setup_workspace(root: Path) -> None:
    (root / "skills" / "test-triage").mkdir(parents=True)
    (root / "skills" / "test-triage" / "SKILL.md").write_text(
        "---\nname: test-triage\ndescription: Triage a failing test suite step by step\n---\n"
        "# Test triage\n1. Run the suite once, capture output.\n2. grep for FAILED.\n3. Delegate root-causing.\n"
    )
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def parse_ids(raw):\n    return [int(x) for x in raw.split(',')][1:]  # BUG: drops first id\n")
    (root / "logs").mkdir()
    (root / "logs" / "ci.log").write_text("\n".join(f"[ci] step {i}: " + "verbose build output " * 9 for i in range(400)))


def main() -> int:  # noqa: PLR0915 - a linear narrated script reads best top to bottom
    ws = Path(tempfile.mkdtemp(prefix="minideep-ws-"))
    setup_workspace(ws)
    backend = CompositeBackend(default=ShellBackend(ws), routes={"/memories/": StateBackend()})

    main_steps = [
        call("execute", "call_tests", command=BIG_TEST_RUN),
        call("grep", "call_grep", pattern="FAILED", path="/large_tool_results"),
        call("read_file", "call_skill", file_path="/skills/test-triage/SKILL.md", limit=1000),
        call("task", "call_task", subagent_type="researcher", description="Find why tests/test_1234 fails. Inspect /src/app.py; write findings to /notes/findings.md."),
        *[call("read_file", f"call_log_{i}", file_path="/logs/ci.log", offset=i * 40, limit=40) for i in range(6)],
        call(
            "edit_file", "call_mem", file_path="/memories/AGENTS.md", old_string="## Preferences", new_string="## Preferences\n- Run pytest with -x when triaging"
        ),
        AIMessage("Root cause: parse_ids() drops the first id (src/app.py:2). Details in /notes/findings.md."),
    ]
    researcher_steps = [
        call("read_file", "r_read", file_path="/src/app.py"),
        call("write_file", "r_write", file_path="/notes/findings.md", content="# Findings\nparse_ids() slices [1:], dropping the first id.\n"),
        AIMessage("Findings written to /notes/findings.md: parse_ids() drops the first id."),
    ]
    main_model, researcher = ScriptedModel(_plan(main_steps)), ScriptedModel(_plan(researcher_steps))
    stats: dict[str, Any] = {"calls": {}}

    def tracer(event: str, **e: Any) -> None:
        who = f"[{e['agent']}]".ljust(13)
        if event == "model_call":
            n = stats["calls"][e["agent"]] = stats["calls"].get(e["agent"], 0) + 1
            print(f"{who}model call #{n:<2} view={e['messages']:>2} msgs  system={e['system_chars']:>4} chars  tools={len(e['tools'])}")
        elif event == "model_result" and EVENT_KEY in e["update"]:
            ev = e["update"][EVENT_KEY]
            print(f"{who}  * SUMMARIZED: cutoff_index={ev['cutoff_index']}, state keeps {len(e['state']['messages'])} msgs, history -> {ev['file_path']}")
        elif event == "tool_call":
            args = ", ".join(f"{k}={str(v)[:38]!r}" for k, v in e["args"].items())
            print(f"{who}  -> {e['tool']}({args})")
        elif event == "tool_result":
            r = e["result"]
            if isinstance(r, Command):
                tm = next(m for m in r.update["messages"] if isinstance(m, ToolMessage))
                print(f"{who}  <- task returned {len(tm.content)} chars (sub-agent transcript stays in the sub-agent)")
            elif "offloaded_to" in r.meta:
                stats["stub_chars"] = len(r.content)
                print(f"{who}  <- {e['tool']}: OFFLOADED to {r.meta['offloaded_to']} (model sees a {len(r.content)}-char stub)")
            else:
                print(f"{who}  <- {e['tool']}: {len(r.content)} chars{' [error]' if r.status == 'error' else ''}")

    agent = create_deep_agent(
        main_model,
        system_prompt="You are a careful coding agent.",
        backend=backend,
        memory=["/memories/AGENTS.md"],
        skills=["/skills/"],
        subagents=[SubAgentSpec("researcher", "Root-causes bugs by reading code", "You investigate code.", model=researcher)],
        # keep-window must sit well below the trigger, or every call re-summarizes
        # (upstream defaults: trigger 85% / keep 10% of the context window).
        summarization_trigger_tokens=7_000,
        summarization_keep_messages=4,
        tracer=tracer,
    )
    print(f"workspace: {ws}\n")
    state = agent.invoke(
        {"messages": [HumanMessage("The test suite is failing. Triage it.")], "files": {"/AGENTS.md": "# Agent memory\n## Preferences\n- Be concise\n"}}
    )
    print(f"\nfinal answer: {final_text(state)}\n")

    offloaded = ws / "large_tool_results" / "call_tests"
    event = state.get(EVENT_KEY) or {}
    last_view = len(main_model.calls[-1].messages) - 1  # minus system message
    checks = [
        ("execute was offered (shell backend)", "execute" in main_model.calls[0].tool_names),
        ("huge execute output offloaded to the backend", offloaded.exists() and len(offloaded.read_text()) > 100_000),
        ("model saw only a short stub of it", 0 < stats.get("stub_chars", 0) < 2_000),
        ("offloaded output is searchable (grep found the failure)", any("test_1234" in m.content for m in state["messages"] if isinstance(m, ToolMessage))),
        ("skill body absent from system prompt until read", "Run the suite once" not in main_model.calls[0].messages[0].content),
        ("sub-agent started with only its task (system + 1 human)", [m.role for m in researcher.calls[0].messages] == ["system", "human"]),
        ("sub-agent's file landed on disk via shared backend", (ws / "notes" / "findings.md").exists()),
        ("summarization fired and recorded an event", bool(event)),
        ("state kept full history; model view is smaller", len(state["messages"]) > last_view),
        ("memory edit routed to state, not disk", "-x" in state["files"]["/AGENTS.md"] and not (ws / "memories").exists()),
    ]
    print("checks:")
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print(f"\nstate messages={len(state['messages'])}  last model view={last_view}  cutoff_index={event.get('cutoff_index')}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
