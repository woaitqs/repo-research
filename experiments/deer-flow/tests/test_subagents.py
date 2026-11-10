"""Isolation, shared sandbox, no nesting, no child checkpointer."""

from minideer.harness import RunContext, assemble_lead
from minideer.messages import AIMessage, call
from minideer.model import ScriptedModel


def test_child_sees_only_its_brief_and_returns_final_text():
    child = ScriptedModel(
        [
            AIMessage("", [call("bash", {"command": "dump"}, "cb")]),
            AIMessage("only the report"),
        ]
    )
    parent = ScriptedModel(
        [
            AIMessage("", [call("task", {"prompt": "write the report"}, "t")]),
            AIMessage("thanks"),
        ]
    )
    ctx = RunContext(thread_id="t")
    ctx.bash_outputs["dump"] = "child-secret-output"
    agent = assemble_lead(parent, ctx=ctx, child_model=child)
    state = agent.run({"messages": []}, user_text="SECRET parent plan")
    transcript = state["_child_transcripts"][0]
    assert transcript[0].content == "write the report"
    assert all("SECRET parent plan" not in m.content for m in transcript)
    task_msg = next(m for m in state["messages"] if m.tool_call_id == "t")
    assert task_msg.content == "only the report"
    assert "child-secret-output" not in task_msg.content
    assert state["_last_child"].checkpointer is False
    assert agent.checkpointer == {}
    assert "task" not in state["_last_child"].tools
    assert "ask_clarification" not in state["_last_child"].tools


def test_child_shares_the_sandbox_and_cannot_narrow_the_skill_view():
    child = ScriptedModel(
        [
            AIMessage("", [call("bash", {"command": "write /outputs/note.txt from-child"}, "w")]),
            AIMessage("wrote it"),
        ]
    )
    parent = ScriptedModel(
        [
            AIMessage("", [call("bash", {"command": "echo"}, "p")]),
            AIMessage("", [call("task", {"prompt": "write a note"}, "t")]),
            AIMessage("done"),
        ]
    )
    ctx = RunContext(thread_id="shared")
    ctx.bash_outputs["echo"] = "parent"
    agent = assemble_lead(parent, ctx=ctx, child_model=child, externalize_min_chars=10_000)
    state = agent.run({"messages": []}, user_text="go")
    box = agent.provider.get(state["sandbox"]["sandbox_id"])
    assert box.read("/outputs/note.txt") == "from-child"
    assert state["sandbox"]["skill_view"] == ["triage"]
    assert state["_last_child"].ctx.thread_id == "shared"


def test_extra_parallel_task_calls_are_dropped():
    child = ScriptedModel([AIMessage("one"), AIMessage("two")])
    parent = ScriptedModel(
        [
            AIMessage(
                "",
                [
                    call("task", {"prompt": "1"}, "a"),
                    call("task", {"prompt": "2"}, "b"),
                    call("task", {"prompt": "3"}, "c"),
                ],
            ),
            AIMessage("done"),
        ]
    )
    ctx = RunContext(thread_id="t")
    agent = assemble_lead(parent, ctx=ctx, child_model=child, max_concurrent_subagents=2)
    state = agent.run({"messages": []}, user_text="go")
    assert ctx.child_runs == 2
    assert [m.tool_call_id for m in state["messages"] if m.name == "task"] == ["a", "b"]
