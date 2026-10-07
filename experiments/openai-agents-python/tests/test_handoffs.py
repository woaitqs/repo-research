"""Handoffs are tools that move control; the default shares the raw transcript."""

from conftest import kinds

from miniagents import (
    Agent,
    HandoffInputData,
    InMemorySession,
    RunConfig,
    Runner,
    ScriptedModel,
    assistant_message,
    function_call,
    handoff,
)


def _pair(child_steps, parent_steps, **handoff_kwargs):
    child = Agent(name="Billing", instructions="billing", handoff_description="refunds", model=ScriptedModel(child_steps))
    target = handoff(child, **handoff_kwargs) if handoff_kwargs else child
    parent = Agent(name="Triage", model=ScriptedModel(parent_steps), handoffs=[target])
    return parent, child


def test_handoff_is_offered_as_a_separate_tool_list(run):
    parent, child = _pair([[assistant_message("done")]], [[function_call("transfer_to_billing", {}, "h1")]])
    result = run(Runner.run(parent, "refund"))
    first = parent.model.calls[0]
    assert first.handoffs == ["transfer_to_billing"] and first.tools == []
    assert result.last_agent is child and result.final_output == "done"


def test_default_handoff_shares_full_raw_history(run):
    parent, child = _pair([[assistant_message("done")]], [[function_call("transfer_to_billing", {}, "h1")]])
    run(Runner.run(parent, "refund"))
    call = child.model.calls[0]
    assert call.system_instructions == "billing"
    assert kinds(call) == ["message:user", "function_call", "function_call_output"]
    assert call.input[-1]["output"] == '{"assistant": "Billing"}'


def test_nested_history_collapses_transcript(run):
    parent, child = _pair([[assistant_message("done")]], [[function_call("transfer_to_billing", {}, "h1")]])
    run(Runner.run(parent, "refund", run_config=RunConfig(nest_handoff_history=True)))
    call = child.model.calls[0]
    assert len(call.input) == 1
    assert "<CONVERSATION HISTORY>" in call.input[0]["content"]


def test_input_filter_changes_model_view_but_not_session_history(run):
    def only_user_messages(data: HandoffInputData) -> HandoffInputData:
        return HandoffInputData(
            input_history=tuple(i for i in data.input_history if i.get("role") == "user"),
            pre_handoff_items=(),
            new_items=(),
        )

    parent, child = _pair(
        [[assistant_message("done")]],
        [[function_call("transfer_to_billing", {}, "h1")]],
        input_filter=only_user_messages,
    )
    session = InMemorySession("s")
    run(Runner.run(parent, "refund", session=session))
    assert kinds(child.model.calls[0]) == ["message:user"]
    stored = run(session.get_items())
    assert [i.get("type") or i.get("role") for i in stored] == [
        "user",
        "function_call",
        "function_call_output",
        "message",
    ]


def test_only_first_of_multiple_handoffs_wins(run):
    other = Agent(name="Sales", model=ScriptedModel([]))
    child = Agent(name="Billing", model=ScriptedModel([[assistant_message("billing")]]))
    parent = Agent(
        name="Triage",
        model=ScriptedModel([[function_call("transfer_to_billing", {}, "h1"), function_call("transfer_to_sales", {}, "h2")]]),
        handoffs=[child, other],
    )
    result = run(Runner.run(parent, "x"))
    assert result.last_agent is child
    ignored = [i.item["output"] for i in result.new_items if i.item.get("call_id") == "h2" and i.kind == "function_call_output"]
    assert ignored == ["Multiple handoffs detected, ignoring this one."]
