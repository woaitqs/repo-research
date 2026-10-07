"""Runtime boundary, output bounding, confirmation, resume and stuck detection."""

import json

import pytest

from mini_openhands import Agent, Conversation, LLMResponse, ScriptedLLM, Status, ToolSpec, tool_call
from mini_openhands.events import SystemPromptEvent, UserRejectObservation
from mini_openhands.workspace import LocalWorkspace


def test_events_are_one_file_each_and_base_state_is_small(make_conversation, tmp_path):
    conv, _ = make_conversation([LLMResponse(text="hello")])
    conv.send_message("hi")
    conv.run()
    root = tmp_path / "conversations" / "conv-1"
    names = sorted(p.name for p in (root / "events").iterdir())
    assert names[0].startswith("event-00000-") and names[0].endswith(".json")
    assert len(names) == 3
    base = json.loads((root / "base_state.json").read_text())
    assert base["status"] == "finished" and "events" not in base


def test_large_output_is_truncated_and_offloaded(make_conversation, tmp_path):
    conv, llm = make_conversation(
        [
            LLMResponse(tool_calls=[tool_call("terminal", {"command": "seq 1 20000"}, "big")]),
            LLMResponse(text="ok"),
        ]
    )
    conv.send_message("print a lot")
    conv.run()
    tool_msg = [m for m in llm.requests[1] if m["role"] == "tool"][0]["content"]
    assert len(tool_msg) < 2_100
    assert "output truncated" in tool_msg and tool_msg.rstrip().endswith("[exit code 0]")
    assert tool_msg.startswith("1\n2\n3\n")  # head is kept ...
    assert "20000" in tool_msg  # ... and so is the tail; the middle is cut
    saved = list((tmp_path / "conversations" / "conv-1" / "observations").glob("terminal_output_*.txt"))
    assert len(saved) == 1 and saved[0].read_text().splitlines()[0] == "1"


def test_confirmation_mode_waits_then_executes_on_next_run(make_conversation, tmp_path):
    conv, _ = make_conversation(
        [
            LLMResponse(tool_calls=[tool_call("terminal", {"command": "touch made.txt"}, "c1")]),
            LLMResponse(text="done"),
        ],
        confirmation_mode=True,
    )
    conv.send_message("make a file")
    conv.run()
    assert conv.state.status == Status.WAITING_FOR_CONFIRMATION
    assert not (tmp_path / "workspace" / "made.txt").exists()
    assert len(conv.state.get_unmatched_actions()) == 1
    conv.run()  # second run() == approval: pending action executes first
    assert (tmp_path / "workspace" / "made.txt").exists()
    assert conv.state.status == Status.FINISHED


def test_rejection_is_an_observation_the_model_reads(make_conversation, tmp_path):
    conv, llm = make_conversation(
        [
            LLMResponse(tool_calls=[tool_call("terminal", {"command": "rm -rf important"}, "c1")]),
            LLMResponse(text="ok, I will not do that"),
        ],
        confirmation_mode=True,
    )
    conv.send_message("clean up")
    conv.run()
    conv.reject_pending_actions("too dangerous")
    conv.run()
    assert any(isinstance(e, UserRejectObservation) for e in conv.state.events)
    tool_msgs = [m for m in llm.requests[1] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == "Action rejected: too dangerous"


def test_resume_from_disk_continues_without_duplicating_system_prompt(tmp_path):
    def build(script, tools=("terminal", "file_editor")):
        agent = Agent(llm=ScriptedLLM(script), tools=[ToolSpec(t) for t in tools])
        return Conversation(agent, tmp_path / "ws", persistence_dir=tmp_path / "convs", conversation_id="abc")

    first = build([LLMResponse(tool_calls=[tool_call("terminal", {"command": "echo 1 > n.txt"}, "c1")]), LLMResponse(text="saved")])
    first.send_message("save a number")
    first.run()
    n_before = len(first.state.events)
    del first  # simulate process exit

    resumed = build([LLMResponse(tool_calls=[tool_call("terminal", {"command": "cat n.txt"}, "c2")]), LLMResponse(text="it is 1")])
    assert len(resumed.state.events) == n_before
    assert resumed.state.status == Status.FINISHED
    resumed.send_message("what was it?")
    resumed.run()
    assert sum(isinstance(e, SystemPromptEvent) for e in resumed.state.events) == 1
    # The resumed model request contains the history from the previous process.
    assert "save a number" in json.dumps(resumed.agent.llm.requests[0])

    with pytest.raises(ValueError, match="tools removed"):
        build([], tools=("terminal",))


def test_stuck_loop_is_detected(make_conversation):
    script = [LLMResponse(tool_calls=[tool_call("terminal", {"command": "echo same"})]) for _ in range(10)]
    conv, llm = make_conversation(script, stuck_threshold=4)
    conv.send_message("loop forever")
    conv.run()
    assert conv.state.status == Status.STUCK
    assert len(llm.requests) == 4  # stopped before burning the rest of the budget


def test_workspace_rejects_path_escape(tmp_path):
    ws = LocalWorkspace(tmp_path / "ws")
    with pytest.raises(ValueError, match="escapes workspace"):
        ws.write_file("../outside.txt", "x")
