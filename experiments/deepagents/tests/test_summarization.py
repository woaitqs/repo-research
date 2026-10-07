"""Summarization: non-destructive event, effective view, safe cutoff, overflow fallback."""

from minideep import AIMessage, HumanMessage, ScriptedModel, ToolCall, ToolMessage, create_deep_agent, is_summary_request, tool
from minideep.summarization import EVENT_KEY, safe_cutoff


@tool
def chunk(i: int) -> str:
    """Return a medium-size chunk."""
    return f"chunk {i}: " + "lorem ipsum " * 40


def make_script(stop_after: int):
    summaries_seen = []
    issued = []  # count calls in a closure: after compaction the model no longer sees them all

    def script(messages, tools, n):
        if is_summary_request(messages):
            return AIMessage(f"SUMMARY#{n}: read some chunks")
        body = [m for m in messages if m.role != "system"]
        if body and body[0].meta.get("lc_source") == "summarization":
            summaries_seen.append(len(messages))
        k = len(issued)
        if k >= stop_after:
            return AIMessage("finished")
        issued.append(k)
        return AIMessage("", tool_calls=[ToolCall("chunk", {"i": k}, f"c{k}")])

    return script, summaries_seen


def test_summarization_keeps_full_history_in_state_and_shrinks_the_model_view():
    script, seen = make_script(stop_after=30)
    model = ScriptedModel(script)
    agent = create_deep_agent(model, tools=[chunk], summarization_trigger_tokens=1_500, summarization_keep_messages=4)
    state = agent.invoke({"messages": [HumanMessage("read chunks")]})

    assert len(state["messages"]) == 1 + 30 * 2 + 1  # nothing was deleted from state
    assert state["messages"][0].content == "read chunks"
    event = state[EVENT_KEY]
    assert event["cutoff_index"] > 0 and event["summary_message"].meta["lc_source"] == "summarization"
    assert seen, "model should have received a summarized view"
    biggest_view = max(len(c.messages) for c in model.calls if not is_summary_request(c.messages))
    assert biggest_view < len(state["messages"])  # bounded context despite unbounded history


def test_evicted_history_is_offloaded_and_chained_summaries_append():
    script, _ = make_script(stop_after=40)
    agent = create_deep_agent(ScriptedModel(script), tools=[chunk], summarization_trigger_tokens=1_200, summarization_keep_messages=4)
    state = agent.invoke({"messages": [HumanMessage("read chunks")]})
    history = [p for p in state["files"] if p.startswith("/conversation_history/")]
    assert len(history) == 1  # one file per session
    text = state["files"][history[0]]
    assert text.count("## Summarized section") >= 2  # chained summarization appended
    assert "SUMMARY#" not in text  # earlier summary messages are not re-offloaded
    assert state[EVENT_KEY]["file_path"] == history[0]


def test_safe_cutoff_never_splits_a_tool_call_from_its_result():
    call = AIMessage("", tool_calls=[ToolCall("x", {}, "t1"), ToolCall("y", {}, "t2")])
    msgs = [HumanMessage("q"), call, ToolMessage("r1", tool_call_id="t1"), ToolMessage("r2", tool_call_id="t2"), AIMessage("a")]
    assert safe_cutoff(msgs, 3) == 1  # cutoff landed on a ToolMessage -> move back to its AIMessage
    assert safe_cutoff(msgs, 4) == 4


def test_context_overflow_from_provider_triggers_summarize_and_retry():
    script, seen = make_script(stop_after=12)
    model = ScriptedModel(script, max_input_tokens=1_400)  # provider limit, far below the proactive trigger
    agent = create_deep_agent(
        model,
        tools=[chunk],
        summarization_trigger_tokens=1_000_000,
        summarization_keep_messages=4,
        trim_tokens_to_summarize=1_000,  # the summary request itself must also fit the window
    )
    state = agent.invoke({"messages": [HumanMessage("read chunks")]})
    assert state["messages"][-1].content == "finished"
    assert EVENT_KEY in state and seen
