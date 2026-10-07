"""Approvals: pause before the side effect, serialize, resume without re-calling the model."""

import json

from conftest import outputs

from miniagents import (
    DEFAULT_REJECTION,
    Agent,
    InMemorySession,
    Runner,
    RunState,
    ScriptedModel,
    assistant_message,
    function_call,
    function_tool,
)


def _agent(steps, executed):
    @function_tool(needs_approval=True)
    def delete_file(path: str) -> str:
        """Delete a file."""
        executed.append(path)
        return f"deleted {path}"

    @function_tool
    def list_files() -> str:
        """List files."""
        executed.append("ls")
        return "a.txt"

    return Agent(name="Ops", model=ScriptedModel(steps), tools=[delete_file, list_files])


def test_interrupt_serialize_approve_resume(run):
    executed = []
    agent = _agent([[function_call("delete_file", {"path": "a.txt"}, "d1")], [assistant_message("deleted")]], executed)
    first = run(Runner.run(agent, "delete a.txt"))
    assert [i.item["name"] for i in first.interruptions] == ["delete_file"]
    assert first.final_output is None and executed == []

    payload = json.loads(json.dumps(first.to_state().to_json()))  # Crosses a process boundary.
    state = RunState.from_json(agent, payload)
    for item in state.get_interruptions():
        state.approve(item)
    second = run(Runner.run(agent, state))

    assert executed == ["a.txt"]
    assert second.final_output == "deleted"
    assert len(agent.model.calls) == 2  # No model call was repeated for the paused turn.
    assert state.current_turn == 2  # Resume continued turn 1; only the follow-up charged a turn.
    assert outputs(agent.model.calls[1]) == ["deleted a.txt"]


def test_reject_sends_rejection_message_instead_of_running(run):
    executed = []
    agent = _agent([[function_call("delete_file", {"path": "a.txt"}, "d1")], [assistant_message("ok, kept")]], executed)
    state = run(Runner.run(agent, "delete a.txt")).to_state()
    state.reject(state.get_interruptions()[0])
    result = run(Runner.run(agent, state))
    assert executed == []
    assert outputs(agent.model.calls[1]) == [DEFAULT_REJECTION]
    assert result.final_output == "ok, kept"


def test_unguarded_sibling_runs_in_the_same_turn(run):
    executed = []
    agent = _agent(
        [
            [function_call("list_files", {}, "l1"), function_call("delete_file", {"path": "a.txt"}, "d1")],
            [assistant_message("done")],
        ],
        executed,
    )
    first = run(Runner.run(agent, "clean up"))
    assert executed == ["ls"]  # The safe tool ran; the guarded one waits.
    state = first.to_state()
    state.approve(state.get_interruptions()[0])
    run(Runner.run(agent, state))
    assert executed == ["ls", "a.txt"]
    assert outputs(agent.model.calls[1]) == ["a.txt", "deleted a.txt"]


def test_paused_turn_is_persisted_once_after_resolution(run):
    executed = []
    session = InMemorySession("s")
    agent = _agent([[function_call("delete_file", {"path": "a.txt"}, "d1")], [assistant_message("deleted")]], executed)
    first = run(Runner.run(agent, "delete a.txt", session=session))
    assert [i.get("role") or i.get("type") for i in run(session.get_items())] == ["user"]
    state = first.to_state()
    state.approve(state.get_interruptions()[0])
    run(Runner.run(agent, state, session=session))
    stored = [i.get("type") or i.get("role") for i in run(session.get_items())]
    assert stored == ["user", "function_call", "function_call_output", "message"]
