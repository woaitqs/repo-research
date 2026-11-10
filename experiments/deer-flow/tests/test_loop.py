"""Loop shape: exit, tool re-entry, static system prompt, reverse after_model."""

from minideer.harness import RunContext, assemble_lead
from minideer.messages import AIMessage, call
from minideer.model import ScriptedModel


def test_loop_exits_when_the_model_stops_calling_tools():
    model = ScriptedModel([AIMessage("done")])
    agent = assemble_lead(model, ctx=RunContext(thread_id="t"))
    state = agent.run({"messages": []}, user_text="hello")
    assert state["_run"]["status"] == "success"
    assert state["messages"][-1].content == "done"
    assert state.get("sandbox") is None


def test_tool_result_is_fed_back_into_the_next_model_call():
    model = ScriptedModel(
        [
            AIMessage("", [call("bash", {"command": "echo"}, "c1")]),
            AIMessage("saw it"),
        ]
    )
    ctx = RunContext(thread_id="t")
    ctx.bash_outputs["echo"] = "ping"
    agent = assemble_lead(model, ctx=ctx)
    state = agent.run({"messages": []}, user_text="go")
    assert state["messages"][-1].content == "saw it"
    assert any(m.role == "tool" and "ping" in m.content for m in model.requests[1].messages)


def test_system_prompt_stays_byte_stable_across_turns():
    model = ScriptedModel([AIMessage("one"), AIMessage("two")])
    agent = assemble_lead(model, ctx=RunContext(thread_id="t"))
    state = agent.run({"messages": []}, user_text="a")
    agent.run(state, user_text="b")
    assert model.requests[0].system == model.requests[1].system == agent.system_prompt
    assert "FULL SKILL BODY" not in agent.system_prompt
    assert "Triage a failing check" in agent.system_prompt


def test_subagent_limit_runs_before_approval():
    """Reverse after_model: the limiter is registered after HITL, so it trims first."""
    model = ScriptedModel(
        [
            AIMessage(
                "",
                [
                    call("task", {"prompt": "a"}, "a"),
                    call("task", {"prompt": "b"}, "b"),
                    call("danger", {}, "d"),
                ],
            )
        ]
    )
    child = ScriptedModel([AIMessage("A"), AIMessage("B")])
    agent = assemble_lead(model, ctx=RunContext(thread_id="t"), child_model=child, max_concurrent_subagents=1)
    state = agent.run({"messages": []}, user_text="go")
    assert state["_run"]["status"] == "waiting_for_input"
    assert state["pause"]["kind"] == "hitl_approval"
    ids = [item["id"] for item in state["messages"][-1].tool_calls]
    assert "a" in ids and "b" not in ids and "d" in ids
    assert "[SUBAGENT LIMIT]" in state["messages"][-1].content
