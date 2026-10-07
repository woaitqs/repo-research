"""Context engineering: the LLM sees a projection (View) of the log;
condensation is an appended event that forgets ids and inserts a summary."""

import json

import pytest

from mini_openhands import LLMResponse, ScriptedLLM, Status, SummarizingCondenser, tool_call
from mini_openhands.events import (
    ActionEvent,
    Condensation,
    CondensationRequest,
    CondensationSummaryEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from mini_openhands.llm import ContextWindowExceededError
from mini_openhands.view import View


def _pair(i):
    a = ActionEvent(tool_name="terminal", tool_call_id=f"c{i}", raw_arguments="{}", arguments={}, llm_response_id=f"r{i}")
    o = ObservationEvent(action_id=a.id, tool_name="terminal", tool_call_id=f"c{i}", content=f"out{i}")
    return [a, o]


def test_view_applies_condensation_and_skips_control_events():
    sp = SystemPromptEvent(system_prompt="sys")
    user = MessageEvent(source="user", text="task")
    pairs = _pair(1) + _pair(2)
    cond = Condensation(forgotten_event_ids=[e.id for e in pairs[:2]], summary="S", summary_offset=2)
    view = View.from_events([sp, user, *pairs, cond, CondensationRequest()])
    assert [type(e).__name__ for e in view.events] == [
        "SystemPromptEvent", "MessageEvent", "CondensationSummaryEvent", "ActionEvent", "ObservationEvent",
    ]
    assert view.unhandled_condensation_request is True


def test_manipulation_indices_never_split_a_tool_call_from_its_result():
    events = [SystemPromptEvent(system_prompt="s"), MessageEvent(source="user", text="t")]
    a1 = ActionEvent(tool_name="terminal", tool_call_id="x", raw_arguments="{}", arguments={}, llm_response_id="r")
    a2 = ActionEvent(tool_name="terminal", tool_call_id="y", raw_arguments="{}", arguments={}, llm_response_id="r")
    o1 = ObservationEvent(action_id=a1.id, tool_name="terminal", tool_call_id="x", content="1")
    o2 = ObservationEvent(action_id=a2.id, tool_name="terminal", tool_call_id="y", content="2")
    view = View(events=[*events, a1, a2, o1, o2])
    # Cutting at 3, 4 or 5 would orphan a call or a result.
    assert view.manipulation_indices() == [0, 1, 2, 6]


def test_condenser_fires_and_full_history_stays_on_disk(make_conversation, tmp_path):
    script = [LLMResponse(tool_calls=[tool_call("terminal", {"command": f"echo step{i}"}, f"c{i}")]) for i in range(6)]
    script.append(LLMResponse(tool_calls=[tool_call("finish", {"message": "done"}, "cf")]))
    summarizer = ScriptedLLM([LLMResponse(text="SUMMARY-1"), LLMResponse(text="SUMMARY-2")])
    conv, llm = make_conversation(script, condenser=SummarizingCondenser(summarizer, max_size=8, keep_first=2))
    conv.send_message("run 6 commands")
    conv.run()

    assert conv.state.status == Status.FINISHED
    condensations = [e for e in conv.state.events if isinstance(e, Condensation)]
    assert condensations, "condensation should have fired"
    first = condensations[0]
    assert first.summary == "SUMMARY-1" and first.summary_offset == 2
    # The condensed view keeps system prompt + first user message, then the summary.
    view = conv.state.view
    assert isinstance(view.events[0], SystemPromptEvent)
    assert isinstance(view.events[1], MessageEvent)
    assert isinstance(view.events[2], CondensationSummaryEvent)
    # Condensation runs before each LLM call, so every request stays bounded
    # (the stored view may exceed max_size until the next step checks it).
    assert all(len(request) <= 8 for request in llm.requests)
    # Nothing was deleted: every event file is still on disk.
    event_dir = tmp_path / "conversations" / "conv-1" / "events"
    assert len(list(event_dir.glob("event-*.json"))) == len(conv.state.events)
    # The model saw the summary inside a user turn (coalesced with the task message).
    after = llm.requests[-1]
    assert after[1]["role"] == "user" and "SUMMARY" in after[1]["content"]
    assert "run 6 commands" in after[1]["content"]
    # The second summarization received the first summary (rolling summary).
    if len(summarizer.requests) > 1:
        assert "SUMMARY-1" in json.dumps(summarizer.requests[1])


def test_context_window_error_triggers_hard_condensation(make_conversation):
    script = [
        LLMResponse(tool_calls=[tool_call("terminal", {"command": "echo 1"}, "c1")]),
        LLMResponse(tool_calls=[tool_call("terminal", {"command": "echo 2"}, "c2")]),
        ContextWindowExceededError("prompt too long"),
        LLMResponse(text="continued after condensing"),
    ]
    summarizer = ScriptedLLM([LLMResponse(text="HARD-SUMMARY")])
    conv, _ = make_conversation(script, condenser=SummarizingCondenser(summarizer, max_size=40, keep_first=2))
    conv.send_message("go")
    conv.run()
    names = [type(e).__name__ for e in conv.state.events]
    assert "CondensationRequest" in names
    assert names.index("Condensation") > names.index("CondensationRequest")
    assert conv.state.status == Status.FINISHED
    assert conv.state.view.unhandled_condensation_request is False


def test_context_window_error_without_condenser_is_fatal(make_conversation):
    conv, _ = make_conversation([ContextWindowExceededError("too long")])
    conv.send_message("go")
    with pytest.raises(ContextWindowExceededError):
        conv.run()
    assert conv.state.status == Status.ERROR
