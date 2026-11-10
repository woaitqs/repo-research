"""Approval pause, clarification end, slash activation, channel tool withholding."""

from minideer.harness import RunContext, assemble_lead
from minideer.messages import AIMessage, call
from minideer.model import ScriptedModel


def test_approval_pauses_the_run_and_resume_can_reject():
    model = ScriptedModel(
        [
            AIMessage("", [call("danger", {}, "d1"), call("bash", {"command": "echo"}, "b1")]),
            AIMessage("after"),
        ]
    )
    ctx = RunContext(thread_id="t")
    ctx.bash_outputs["echo"] = "ran"
    agent = assemble_lead(model, ctx=ctx)
    state = agent.run({"messages": []}, user_text="remember: likes rust")
    assert state["_run"]["status"] == "waiting_for_input"
    assert state["pause"]["kind"] == "hitl_approval"
    assert "likes rust" not in ctx.memory.recall("user-1", "lead")
    agent.run(state, resume={"d1": "reject", "b1": "approve"})
    assert any(m.tool_call_id == "d1" and "rejected" in m.content for m in state["messages"])
    assert any(m.tool_call_id == "b1" and m.content == "ran" for m in state["messages"])
    assert state["_run"]["status"] == "success"
    assert state["messages"][-1].content == "after"


def test_non_interactive_approval_denies_instead_of_pausing():
    model = ScriptedModel(
        [
            AIMessage("", [call("danger", {}, "d1")]),
            AIMessage("continued"),
        ]
    )
    agent = assemble_lead(model, ctx=RunContext(thread_id="t", interactive=False))
    state = agent.run({"messages": []}, user_text="go")
    assert state.get("pause") is None
    assert state["_run"]["status"] == "success"
    assert any(m.tool_call_id == "d1" and "denied" in m.content for m in state["messages"])
    assert state["messages"][-1].content == "continued"


def test_clarification_ends_the_turn_and_is_not_a_resume():
    model = ScriptedModel(
        [
            AIMessage("", [call("ask_clarification", {"question": "which file?"}, "q")]),
            AIMessage("should not run yet"),
        ]
    )
    agent = assemble_lead(model, ctx=RunContext(thread_id="t"))
    state = agent.run({"messages": []}, user_text="go")
    assert state["needs_user"] is True
    assert state["_run"]["status"] == "success"
    assert any("which file?" in m.content for m in state["messages"])
    assert len(model.script) == 1
    try:
        agent.run(state, resume={"q": "approve"})
    except RuntimeError as exc:
        assert "clarification" in str(exc)
    else:
        raise AssertionError("resume should be rejected")


def test_disabled_clarification_lets_the_loop_continue():
    model = ScriptedModel(
        [
            AIMessage("", [call("ask_clarification", {"question": "which file?"}, "q")]),
            AIMessage("continued"),
        ]
    )
    ctx = RunContext(thread_id="t", interactive=False)
    agent = assemble_lead(model, ctx=ctx)
    assert "ask_clarification" not in agent.tools
    agent.tools["ask_clarification"] = lambda request: "should not be called"
    state = agent.run({"messages": []}, user_text="go")
    assert state["needs_user"] is False
    assert any("proceed without asking" in m.content for m in state["messages"])
    assert state["messages"][-1].content == "continued"


def test_slash_skill_is_injected_once_and_not_stored():
    model = ScriptedModel(
        [
            AIMessage("", [call("bash", {"command": "echo"}, "b")]),
            AIMessage("done"),
        ]
    )
    ctx = RunContext(thread_id="t")
    ctx.bash_outputs["echo"] = "ok"
    agent = assemble_lead(model, ctx=ctx, externalize_min_chars=10_000)
    state = agent.run({"messages": []}, user_text="/triage the failure")
    assert sum("FULL SKILL BODY" in m.content for m in model.requests[0].messages) == 1
    assert all("FULL SKILL BODY" not in m.content for m in model.requests[1].messages)
    assert all("FULL SKILL BODY" not in m.content for m in state["messages"])


def test_github_channel_does_not_receive_update_agent():
    agent = assemble_lead(ScriptedModel([AIMessage("ok")]), ctx=RunContext(thread_id="t", channel="github"))
    assert "update_agent" not in agent.tools
    other = assemble_lead(ScriptedModel([AIMessage("ok")]), ctx=RunContext(thread_id="u"))
    assert "update_agent" in other.tools


def test_tool_mode_does_not_inject_facts_and_exposes_search():
    ctx = RunContext(thread_id="t", memory_mode="tool")
    ctx.memory.add("user-1", "lead", "likes python")
    model = ScriptedModel(
        [
            AIMessage("", [call("memory_search", {}, "m")]),
            AIMessage("found"),
        ]
    )
    agent = assemble_lead(model, ctx=ctx)
    state = agent.run({"messages": []}, user_text="what do you know?")
    reminder = next(m for m in state["messages"] if m.extra.get("kind") == "full-reminder")
    assert "<memory></memory>" in reminder.content
    assert any(m.tool_call_id == "m" and "likes python" in m.content for m in state["messages"])
