"""A scripted workbench turn: render, one-step Pi, ReAct tools, archive, compact."""

from __future__ import annotations

from minispace.compaction import apply_compaction, next_compact_path, plan_compaction
from minispace.evaluation import save_evaluation
from minispace.model import ScriptedModel
from minispace.storage import Home
from minispace.subagent import build_subagent_thread
from minispace.tools import Tool
from minispace.workbench import Workbench


def _skill(_tool: Tool, arguments: dict, _runtime: str) -> str:
    return f"SKILL {arguments['name']}: classify, then list files."


def _bash(_tool: Tool, arguments: dict, _runtime: str) -> str:
    return f"ran: {arguments['command']}"


def main() -> None:
    checks: list[str] = []

    def check(name: str, ok: bool) -> None:
        checks.append(name)
        if not ok:
            raise SystemExit(f"demo check failed: {name}")

    tools = [
        Tool("skill", "builtin"),
        Tool("bash", "builtin"),
        Tool("lookup", "function"),
        Tool("spawn_agent", "builtin", terminate=True),
    ]
    model = ScriptedModel(
        [
            {"tool_calls": [{"name": "skill", "arguments": {"name": "triage"}}]},
            {"tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}]},
            {"text": "triage complete"},
        ]
    )
    thread = {
        "system": "Today is {{current_date}}. You triage files.",
        "messages": [{"role": "user", "text": "Triage the inbox."}],
        "values": {"current_date": "2026-11-10"},
        "snapshot": {},
        "tools": tools,
        "runs": [],
        "evaluations": [],
    }
    bench = Workbench(
        model,
        tools,
        {"skill": _skill, "bash": _bash},
    )
    result = bench.run(thread, mode="react")
    check("stored template unchanged", thread["system"].count("current_date") == 1 and "{{" in thread["system"])
    check("request saw the rendered date", "2026-11-10" in bench.model.requests[0]["system"])
    check("pi executed nothing", result["pi_executed"] == [])
    check("three model steps", result["model_calls"] == 3)
    check("skill then bash", [item["name"] for item in result["tool_executions"]] == ["skill", "bash"])
    check("ended on model text", result["stop"] == "model_done")
    check("run recorded", result["run_record"] is not None)

    original_system = thread["system"]
    thread["snapshot"] = result["snapshot"]
    thread["messages"] = result["messages"]
    thread["runs"] = [result["run_record"]]
    home = Home()
    home.save("inbox.json", thread)
    stored = home.workspace["inbox.json"]
    check("stored template unchanged", thread["system"] == original_system)
    check("workspace has no run body", "runs" not in stored and stored["run_index"])
    check("history holds the snapshot", bool(home.read_run("inbox.json", stored["run_index"][0])["id"]))

    # A later day must not rewrite the frozen system place.
    later = Workbench(ScriptedModel([{"text": "still fine"}]), tools)
    continued = {
        **thread,
        "messages": [*result["messages"], {"role": "user", "text": "Anything else?"}],
        "snapshot": result["snapshot"],
    }
    later.run(continued, mode="step", values={"current_date": "2026-11-11"})
    check("snapshot froze the first date", "2026-11-10" in later.model.requests[0]["system"])
    check("second day did not leak", "2026-11-11" not in later.model.requests[0]["system"])

    sample = [
        {"role": "user", "text": "one"},
        {"role": "assistant", "text": "a"},
        {"role": "user", "text": "two"},
        {"role": "assistant", "text": "b"},
        {"role": "user", "text": "three"},
        {"role": "assistant", "text": "c"},
    ]
    before = list(sample)
    plan = plan_compaction(sample, 1)
    compacted = apply_compaction(plan, "Goal: triage the inbox.")
    sibling = next_compact_path("inbox.json", {"inbox.json"})
    check("compaction keeps the last turn", plan["kept_turn_count"] == 1 and plan["kept"][0]["text"] == "three")
    check("source messages untouched", sample == before)
    check("sibling name", sibling == "inbox-compact-1.json")
    check("checkpoint is a user message", compacted[0]["role"] == "user" and compacted[0].get("checkpoint"))

    child = build_subagent_thread(
        {**thread, "messages": result["messages"], "tools": tools},
        parent_name="inbox",
        task_name="read logs",
        prompt="Read the inbox log.",
        role="researcher",
    )
    check("child not executed", child["executed"] is False)
    check("child drops parent transcript", "Triage the inbox." not in child["messages"][-1]["text"])
    check("child drops spawn_agent", all(tool.name != "spawn_agent" for tool in child["tools"]))

    calls_before = len(later.model.requests)
    save_evaluation(
        stored,
        left_run_id="a",
        right_run_id="b",
        rubric={"id": "rubric", "revision": 1, "criteria": ["clarity"]},
        scores={"a": {"clarity": 3}, "b": {"clarity": 5}},
        verdict="right",
    )
    check("evaluation did not call the model", later.model.requests and calls_before == 1)
    check("rubric snapshot stored", stored["evaluations"][0]["rubric"]["revision"] == 1)

    print(f"minispace demo: {len(checks)}/{len(checks)} checks passed")
    for name in checks:
        print(f"  ok  {name}")


if __name__ == "__main__":
    main()
