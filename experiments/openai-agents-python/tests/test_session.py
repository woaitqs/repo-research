"""Sessions are an append-only log that the runner prepends to each run's input."""

from conftest import kinds

from miniagents import (
    Agent,
    InMemorySession,
    Runner,
    ScriptedModel,
    SessionSettings,
    SQLiteSession,
    ToolOutputTrimmer,
    RunConfig,
    assistant_message,
)


def test_history_is_prepended_on_the_next_run(run):
    model = ScriptedModel([[assistant_message("Hi Ada")], [assistant_message("Ada")]])
    agent = Agent(name="A", model=model)
    session = InMemorySession("s")
    run(Runner.run(agent, "My name is Ada", session=session))
    run(Runner.run(agent, "What is my name?", session=session))
    assert kinds(model.calls[1]) == ["message:user", "message:assistant", "message:user"]


def test_limit_keeps_only_newest_history(run):
    model = ScriptedModel([[assistant_message("a")], [assistant_message("b")]])
    agent = Agent(name="A", model=model)
    session = InMemorySession("s", SessionSettings(limit=1))
    run(Runner.run(agent, "first", session=session))
    run(Runner.run(agent, "second", session=session))
    assert [i.get("content") for i in model.calls[1].input] == ["a", "second"]


def test_sqlite_session_survives_a_new_session_object(run, tmp_path):
    db = str(tmp_path / "s.db")
    model = ScriptedModel([[assistant_message("noted")], [assistant_message("blue")]])
    agent = Agent(name="A", model=model)
    run(Runner.run(agent, "favourite colour is blue", session=SQLiteSession("u1", db)))
    run(Runner.run(agent, "favourite colour?", session=SQLiteSession("u1", db)))
    assert model.calls[1].input[0]["content"] == "favourite colour is blue"
    assert run(SQLiteSession("other", db).get_items()) == []


def test_manual_memory_with_to_input_list_matches_session(run):
    model = ScriptedModel([[assistant_message("Hi")], [assistant_message("again")]])
    agent = Agent(name="A", model=model)
    first = run(Runner.run(agent, "hello"))
    run(Runner.run(agent, first.to_input_list() + [{"role": "user", "content": "hello?"}]))
    assert kinds(model.calls[1]) == ["message:user", "message:assistant", "message:user"]


def test_trimmer_shrinks_old_tool_outputs_only_in_the_model_view(run):
    history = [
        {"role": "user", "content": "old"},
        {"type": "function_call", "name": "search", "arguments": "{}", "call_id": "o1"},
        {"type": "function_call_output", "call_id": "o1", "output": "X" * 5000},
        {"role": "user", "content": "new"},
    ]
    model = ScriptedModel([[assistant_message("ok")]])
    config = RunConfig(call_model_input_filter=ToolOutputTrimmer(recent_turns=1, max_output_chars=500))
    run(Runner.run(Agent(name="A", model=model), list(history), run_config=config))
    sent = model.calls[0].input[2]["output"]
    assert sent.startswith("[Trimmed: search output — 5000 chars") and len(sent) < 300
    assert len(history[2]["output"]) == 5000
