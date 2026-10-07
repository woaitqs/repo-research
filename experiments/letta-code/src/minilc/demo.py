"""Narrated end-to-end demo of the letta-code architecture in miniature.

No network: `ScriptedModel` plays the LLM and records every request, so each
printed claim is checked against what the "model" actually received.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from .backend import LocalBackend
from .harness import HEADLESS_DENY, Harness
from .model import ScriptedModel
from .permissions import PermissionPolicy
from .subagents import agent_tool, child_factory
from .tools import ToolContext, ToolRegistry, default_tools

MEMORY = {
    "MEMORY.md": "# Memory\n- [projects](projects/MEMORY.md)\n",
    "human.md": "---\nname: human\ndescription: About the user\n---\nPrefers short answers.\n",
    "projects/MEMORY.md": "# Projects\n",
}


def call(cid, name, **args):
    return {"text": "", "tool_calls": [{"id": cid, "name": name, "arguments": args}]}


def main() -> int:
    root = tempfile.mkdtemp(prefix="minilc-demo-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    with open(os.path.join(work, "notes.txt"), "w") as fh:
        fh.write("project notes\nmagic number: 4217\n")
    checks: list[tuple[str, bool]] = []

    def check(label: str, ok: bool) -> None:
        checks.append((label, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    memory_dir_holder: dict[str, str] = {}

    def commit_memory(_request):
        # the "model" edits a core memory file and commits it via the shell tool
        return call("m2", "Bash", command=f"cd {memory_dir_holder['dir']} && git -c user.name=a -c user.email=a@x "
                                          f"commit -qam 'memory: favorite color'")

    script = [
        # 1. tool loop
        call("c1", "Read", file_path="notes.txt"),
        call("c2", "Write", file_path="answer.txt", content="4217"),
        {"text": "4217"},
        # 2. permissions (standard mode)
        call("c3", "Bash", command="rm -rf build"),
        {"text": "I was not allowed to run that."},
        # 3. memory write + delta
        lambda req: call("m1", "Edit", file_path=os.path.join(memory_dir_holder["dir"], "human.md"),
                         old_string="Prefers short answers.", new_string="Prefers short answers. Favorite color: teal."),
        commit_memory,
        {"text": "Saved."},
        {"text": "noted"},          # next turn in the same conversation
        {"text": "teal"},           # new conversation
        # 4. big output
        call("c4", "Bash", command="python3 -c \"print('x'*100000)\""),
        {"text": "that was long"},
        # 5. sub-agent
        call("s1", "Agent", prompt="Count the lines in notes.txt and report.", subagent_type="general-purpose"),
        call("k1", "Bash", command="wc -l < notes.txt"),   # child step 1
        {"text": "notes.txt has 2 lines"},                 # child step 2 (report)
        {"text": "The subagent says 2 lines."},
        # 6. compaction summary
        {"text": "SUMMARY: user asked for the magic number (4217), saved teal as favorite color."},
    ]
    model = ScriptedModel(script)
    backend = LocalBackend(os.path.join(root, "store"), model)
    agent = backend.create_agent("demo", "You are a stateful coding agent.\n{CORE_MEMORY}", MEMORY)
    memory_dir_holder["dir"] = agent["memory_dir"]
    conv = backend.create_conversation(agent["id"])
    ctx = ToolContext(cwd=work, overflow_dir=os.path.join(root, "overflow"))
    file_tools = ToolRegistry(default_tools())
    tools = ToolRegistry([*default_tools(), agent_tool(child_factory(backend, agent, file_tools, work,
                                                                     os.path.join(root, "overflow")))])
    policy = PermissionPolicy(mode="unrestricted", cwd=work)
    harness = Harness(backend, agent, tools, ctx, policy)

    print("\n1) Split loop: the backend runs ONE model step per run; the client executes tools.")
    r = harness.run(conv, "Find the magic number and save it to answer.txt.")
    stops = [e["stop_reason"] for e in r.events if e["message_type"] == "stop_reason"]
    print(f"   stop reasons per run: {stops}")
    check("each tool step ends its run with requires_approval, the last with end_turn",
          stops == ["requires_approval", "requires_approval", "end_turn"])
    check("the client wrote answer.txt (tools ran client-side)",
          open(os.path.join(work, "answer.txt")).read() == "4217")
    check("system prompt bytes identical across the 3 provider calls",
          len({req["system"] for req in model.requests[:3]}) == 1)

    print("\n2) Permissions are classified client-side before execution (standard mode, headless).")
    harness.policy = PermissionPolicy(mode="standard", cwd=work)
    harness.run(conv, "Clean the build directory.")
    denied = model.requests[4]["messages"][-1]
    check("an 'ask' decision in one-shot headless becomes a denied tool result the model can read",
          denied["role"] == "toolResult" and denied["content"] == HEADLESS_DENY)
    harness.policy = policy

    print("\n3) Memory = git repo compiled from HEAD; a commit reaches the model once as a delta.")
    harness.run(conv, "Remember my favorite color is teal.")
    after_commit = model.requests[7]
    check("the call after the commit carries a <memory_update> with the new fact",
          "<memory_update>" in json.dumps(after_commit["messages"]) and "teal" in json.dumps(after_commit["messages"][-1]))
    harness.run(conv, "ok")
    later = model.requests[8]
    check("(upstream behaviour) the next turn no longer sees it in prompt or delta",
          "Favorite color: teal" not in later["system"] and "<memory_update>" not in json.dumps(later["messages"]))
    conv2 = backend.create_conversation(agent["id"])
    harness.run(conv2, "What is my favorite color?")
    check("a new conversation compiles the committed memory into its system prompt",
          "Favorite color: teal" in model.requests[9]["system"])

    print("\n4) Big observations are clamped; the full text is written to an overflow file.")
    harness.run(conv2, "Print a lot.")
    returned = model.requests[11]["messages"][-1]["content"]
    check("tool result clamped to ~32k chars with an overflow pointer",
          len(returned) < 33_000 and "[Full output written to:" in returned)

    print("\n5) Sub-agent: fresh child, own conversation, only the report comes back.")
    harness.run(conv2, "Delegate the line count.")
    child_first = model.requests[13]
    check("child's first request holds only its brief, without the parent's memory",
          [m["role"] for m in child_first["messages"]] == ["user"] and "Favorite color" not in child_first["system"])
    check("parent receives only the child's final report as the Agent tool result",
          model.requests[15]["messages"][-1]["content"] == "notes.txt has 2 lines")

    print("\n6) Compaction is append-only: the transcript keeps everything, the view shrinks.")
    transcript = backend.transcript(conv2)
    rows_before = len(transcript.rows())
    result = backend.compact(conv2, mode="all")
    rows = transcript.rows()
    check("one compaction row appended, earlier rows untouched, view = summary + kept",
          len(rows) == rows_before + 1 and rows[-1]["type"] == "compaction"
          and len(transcript.view()) == result["num_messages_after"])

    passed = sum(ok for _, ok in checks)
    print(f"\n{passed}/{len(checks)} checks passed  (artifacts in {root})")
    subprocess.run(["git", "log", "--oneline"], cwd=agent["memory_dir"])
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
