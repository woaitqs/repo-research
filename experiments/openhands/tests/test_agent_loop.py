"""Agent loop: tool call -> ActionEvent -> executor -> ObservationEvent -> next
LLM request sees the result; errors become model-visible events."""

from mini_openhands import LLMResponse, Status, tool_call
from mini_openhands.events import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)


def kinds(conv):
    return [type(e).__name__ for e in conv.state.events]


def test_tool_call_observation_and_finish(make_conversation):
    conv, llm = make_conversation(
        [
            LLMResponse(text="create it", tool_calls=[tool_call("file_editor", {"command": "create", "path": "hi.py", "file_text": "print('hi')\n"}, "c1")]),
            LLMResponse(tool_calls=[tool_call("terminal", {"command": "python3 hi.py"}, "c2")]),
            LLMResponse(tool_calls=[tool_call("finish", {"message": "done"}, "c3")]),
        ]
    )
    conv.send_message("make hi.py and run it")
    conv.run()

    assert conv.state.status == Status.FINISHED
    assert kinds(conv) == [
        "SystemPromptEvent", "MessageEvent",
        "ActionEvent", "ObservationEvent",
        "ActionEvent", "ObservationEvent",
        "ActionEvent", "ObservationEvent",
    ]
    # Context update: the 3rd request contains the terminal result as a tool message.
    third_request = llm.requests[2]
    tool_msgs = [m for m in third_request if m["role"] == "tool"]
    assert tool_msgs[-1]["tool_call_id"] == "c2"
    assert "hi" in tool_msgs[-1]["content"] and "[exit code 0]" in tool_msgs[-1]["content"]
    # The system prompt is always the first message.
    assert third_request[0]["role"] == "system"


def test_plain_text_answer_finishes(make_conversation):
    conv, _ = make_conversation([LLMResponse(text="42")])
    conv.send_message("answer")
    conv.run()
    assert conv.state.status == Status.FINISHED
    last = conv.state.events[len(conv.state.events) - 1]
    assert isinstance(last, MessageEvent) and last.source == "agent" and last.text == "42"


def test_empty_response_gets_corrective_nudge(make_conversation):
    conv, llm = make_conversation([LLMResponse(text=""), LLMResponse(text="ok")])
    conv.send_message("go")
    conv.run()
    nudges = [e for e in conv.state.events if isinstance(e, MessageEvent) and e.source == "environment"]
    assert len(nudges) == 1
    assert llm.requests[1][-1]["role"] == "user"  # nudge reaches the model as role=user


def test_bad_tool_calls_become_agent_errors_and_loop_continues(make_conversation):
    conv, llm = make_conversation(
        [
            LLMResponse(tool_calls=[tool_call("does_not_exist", {}, "c1")]),
            LLMResponse(tool_calls=[tool_call("terminal", '{"command": "ls" ', "c2")]),
            LLMResponse(tool_calls=[tool_call("terminal", {"cmd": "ls"}, "c3")]),
            LLMResponse(tool_calls=[tool_call("file_editor", {"command": "view", "path": "missing.txt"}, "c4")]),
            LLMResponse(text="recovered"),
        ]
    )
    conv.send_message("go")
    conv.run()

    errors = [e for e in conv.state.events if isinstance(e, AgentErrorEvent)]
    assert [e.tool_call_id for e in errors] == ["c1", "c2", "c3", "c4"]
    assert "not found" in errors[0].error
    assert "Expecting" in errors[1].error  # JSON decode error text
    assert "missing required" in errors[2].error
    assert "Error executing tool 'file_editor'" in errors[3].error
    # Non-executable calls are still recorded, so tool_call/tool_result pairs stay valid.
    bad_actions = [e for e in conv.state.events if isinstance(e, ActionEvent) and e.arguments is None]
    assert len(bad_actions) == 3
    assert conv.state.status == Status.FINISHED
    # Every assistant tool_call in the final request is answered by a tool message.
    final = llm.requests[-1]
    call_ids = [tc["id"] for m in final if m.get("tool_calls") for tc in m["tool_calls"]]
    answered = [m["tool_call_id"] for m in final if m["role"] == "tool"]
    assert call_ids == answered


def test_parallel_tool_calls_replay_as_one_assistant_message(make_conversation):
    conv, llm = make_conversation(
        [
            LLMResponse(
                text="two at once",
                tool_calls=[
                    tool_call("terminal", {"command": "echo a"}, "p1"),
                    tool_call("terminal", {"command": "echo b"}, "p2"),
                ],
            ),
            LLMResponse(text="done"),
        ]
    )
    conv.send_message("go")
    conv.run()
    assistant_with_calls = [m for m in llm.requests[1] if m.get("tool_calls")]
    # Stored as two ActionEvents, sent as one assistant message with two calls.
    assert len(assistant_with_calls) == 1
    assert [tc["id"] for tc in assistant_with_calls[0]["tool_calls"]] == ["p1", "p2"]


def test_max_iterations_sets_error(make_conversation):
    script = [LLMResponse(tool_calls=[tool_call("terminal", {"command": f"echo {i}"})]) for i in range(5)]
    conv, _ = make_conversation(script, max_iterations=3)
    conv.send_message("loop")
    conv.run()
    assert conv.state.status == Status.ERROR
    assert type(conv.state.events[len(conv.state.events) - 1]).__name__ == "ConversationErrorEvent"


def test_system_prompt_emitted_once(make_conversation):
    conv, _ = make_conversation([LLMResponse(text="a"), LLMResponse(text="b")])
    conv.send_message("1")
    conv.run()
    conv.send_message("2")
    conv.run()
    assert sum(isinstance(e, SystemPromptEvent) for e in conv.state.events) == 1
    assert sum(isinstance(e, ObservationEvent) for e in conv.state.events) == 0
