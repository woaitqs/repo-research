"""Frozen reminder, destructive summary, offload, dangling calls."""

from minideer.harness import RunContext, assemble_lead
from minideer.messages import AIMessage, call
from minideer.model import ScriptedModel


def _reminder(state, kind="full-reminder"):
    return [m for m in state["messages"] if m.extra.get("kind") == kind]


def test_memory_reminder_is_frozen_for_the_rest_of_the_day():
    ctx = RunContext(thread_id="t")
    ctx.memory.add("user-1", "lead", "likes python")
    model = ScriptedModel([AIMessage("ok"), AIMessage("ok2")])
    agent = assemble_lead(model, ctx=ctx)
    state = agent.run({"messages": []}, user_text="remember: likes rust")
    assert "likes python" in _reminder(state)[0].content
    assert "likes rust" not in _reminder(state)[0].content
    assert "likes rust" in ctx.memory.recall("user-1", "lead")
    agent.run(state, user_text="next")
    assert len(_reminder(state)) == 1
    assert _reminder(state, "date-update") == []
    assert "likes rust" not in _reminder(state)[0].content


def test_midnight_injects_a_date_line_and_does_not_refresh_memory():
    ctx = RunContext(thread_id="t")
    ctx.memory.add("user-1", "lead", "likes python")
    model = ScriptedModel([AIMessage("ok"), AIMessage("next-day")])
    agent = assemble_lead(model, ctx=ctx)
    state = agent.run({"messages": []}, user_text="remember: likes rust")
    ctx.today = "2026-11-11"
    agent.run(state, user_text="morning")
    updates = _reminder(state, "date-update")
    assert len(updates) == 1
    assert "<memory>" not in updates[0].content
    assert "2026-11-11" in updates[0].content
    assert "likes rust" not in updates[0].content


def test_summarization_deletes_history_and_reinjects_summary_only_on_the_request():
    ctx = RunContext(thread_id="t")
    model = ScriptedModel([AIMessage("after")])
    agent = assemble_lead(model, ctx=ctx, summary_trigger=4, summary_keep=2)
    messages = []
    for i in range(6):
        messages.append(__import__("minideer.messages", fromlist=["HumanMessage"]).HumanMessage(f"fact-{i}"))
    state = agent.run({"messages": messages}, user_text="now")
    assert all(not m.content.startswith("fact-0") for m in state["messages"])
    assert "fact-0" in state["summary_text"]
    assert any("fact-0" in m.content and m.extra.get("kind") == "durable-context" for m in model.requests[0].messages)
    assert all(m.extra.get("kind") != "durable-context" for m in state["messages"])


def test_dangling_tool_call_is_settled_before_the_model_call():
    from minideer.messages import AIMessage as AI
    from minideer.messages import call as mk

    model = ScriptedModel([AIMessage("recovered")])
    agent = assemble_lead(model, ctx=RunContext(thread_id="t"))
    dangling = AI("", [mk("bash", {"command": "echo"}, "lost")])
    state = agent.run({"messages": [dangling]}, user_text="continue")
    assert any(m.tool_call_id == "lost" and "dangling" in m.content for m in state["messages"])
    assert any(m.tool_call_id == "lost" for m in model.requests[0].messages)


def test_oversized_tool_output_is_externalized_and_readable():
    payload = "HEAD" + ("x" * 100) + "TAIL"
    ctx = RunContext(thread_id="t")
    ctx.bash_outputs["dump"] = payload
    model = ScriptedModel(
        [
            AIMessage("", [call("bash", {"command": "dump"}, "big")]),
            AIMessage("done"),
        ]
    )
    agent = assemble_lead(model, ctx=ctx, externalize_min_chars=40)
    state = agent.run({"messages": []}, user_text="go")
    stub = next(m for m in state["messages"] if m.tool_call_id == "big")
    assert "externalized" in stub.content
    assert payload not in stub.content
    stored = agent.provider.get(state["sandbox"]["sandbox_id"]).read("/tool-outputs/big.txt")
    assert stored == payload


def test_a_text_only_turn_does_not_acquire_a_sandbox():
    model = ScriptedModel([AIMessage("hi")])
    agent = assemble_lead(model, ctx=RunContext(thread_id="t"))
    state = agent.run({"messages": []}, user_text="hi")
    assert state.get("sandbox") is None
