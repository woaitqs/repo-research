"""Narrated scripted run. Exits non-zero if an invariant fails."""

from __future__ import annotations

from minideer.harness import RunContext, assemble_lead
from minideer.messages import AIMessage, call
from minideer.model import ScriptedModel


def main() -> None:
    ctx = RunContext(thread_id="demo", today="2026-11-10")
    ctx.memory.add("user-1", "lead", "likes python")
    ctx.bash_outputs["dump-log"] = "ERROR boom\n" + ("x" * 200)
    child = ScriptedModel(
        [
            AIMessage("", [call("bash", {"command": "write /outputs/report.md child-report"}, "w1")]),
            AIMessage("report written to /outputs/report.md"),
        ]
    )
    parent = ScriptedModel(
        [
            AIMessage("", [call("bash", {"command": "dump-log"}, "b1")]),
            AIMessage("", [call("read_file", {"path": "/tool-outputs/b1.txt"}, "r1")]),
            AIMessage("", [call("task", {"prompt": "summarize the log"}, "t1")]),
            AIMessage("The log shows ERROR boom. Report saved."),
        ]
    )
    agent = assemble_lead(
        parent,
        ctx=ctx,
        child_model=child,
        externalize_min_chars=80,
        summary_trigger=100,
    )
    state: dict = {"messages": []}
    agent.run(state, user_text="/triage remember: likes rust. The log is huge.")

    checks = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, ok))
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")

    print("minideer demo")
    prompt = agent.system_prompt
    check("system prompt is skill metadata only", "FULL SKILL BODY" not in prompt and "Triage a failing check" in prompt)
    check("system prompt unchanged on the request", all(req.system == prompt for req in parent.requests))
    reminder = next(m for m in state["messages"] if m.extra.get("kind") == "full-reminder")
    check("frozen reminder has the pre-run fact", "likes python" in reminder.content and "likes rust" not in reminder.content)
    check("slash body reached the model once", sum("FULL SKILL BODY" in m.content for req in parent.requests for m in req.messages) == 1)
    check("slash body was not stored", all("FULL SKILL BODY" not in m.content for m in state["messages"]))
    offloaded = next(m for m in state["messages"] if m.tool_call_id == "b1")
    check("oversized bash output was externalized", "externalized" in offloaded.content and "/tool-outputs/b1.txt" in offloaded.content)
    box = agent.provider.get(state["sandbox"]["sandbox_id"])
    check("full output is in the sandbox", "ERROR boom" in box.read("/tool-outputs/b1.txt") and len(box.read("/tool-outputs/b1.txt")) > 200)
    child_transcript = state["_child_transcripts"][0]
    check("child did not see the parent prompt", all("log is huge" not in m.content for m in child_transcript))
    task_msg = next(m for m in state["messages"] if m.tool_call_id == "t1")
    check("parent observes only the child's final text", task_msg.content == "report written to /outputs/report.md")
    check("child wrote through the shared sandbox", box.read("/outputs/report.md") == "child-report")
    check("child has no checkpointer", state["_last_child"].checkpointer is False)
    check("durable contract is not stored on the thread", all("Durable context authority contract" not in m.content for m in state["messages"]))
    check("run completed", state["_run"]["status"] == "success")
    check("skill view is the lead's", state["sandbox"]["skill_view"] == ["triage"])

    failed = [name for name, ok in checks if not ok]
    print(f"{len(checks) - len(failed)}/{len(checks)} invariants held")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
