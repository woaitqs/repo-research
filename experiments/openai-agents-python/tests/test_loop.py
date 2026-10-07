"""The run loop: NextStep transitions, errors as observations, turn accounting."""

import asyncio
import json
import time

import pytest
from conftest import kinds, outputs

from miniagents import (
    DEFAULT_TOOL_ERROR,
    Agent,
    MaxTurnsExceeded,
    ModelBehaviorError,
    RunConfig,
    Runner,
    ScriptedModel,
    assistant_message,
    function_call,
    function_tool,
)


@function_tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def test_message_without_tool_calls_is_final(run):
    model = ScriptedModel([[assistant_message("hello")]])
    result = run(Runner.run(Agent(name="A", model=model), "hi"))
    assert result.final_output == "hello"
    assert len(model.calls) == 1


def test_tool_output_is_fed_back_and_model_runs_again(run):
    model = ScriptedModel([[function_call("add", {"a": 2, "b": 3}, "c1")], [assistant_message("5")]])
    result = run(Runner.run(Agent(name="A", model=model, tools=[add]), "2+3?"))
    assert result.final_output == "5"
    assert kinds(model.calls[1]) == ["message:user", "function_call", "function_call_output"]
    assert outputs(model.calls[1]) == ["5"]
    assert model.calls[0].tools == ["add"]


def test_tool_schema_is_strict_and_derived_from_signature():
    assert add.params_json_schema == {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }


def test_tool_exception_becomes_redacted_observation(run):
    @function_tool
    def lookup(order_id: str) -> str:
        """Look up an order."""
        raise RuntimeError("password=hunter2")

    model = ScriptedModel(
        [
            [function_call("lookup", {"order_id": "1"}, "c1"), function_call("lookup", "{bad json", "c2")],
            [assistant_message("sorry")],
        ]
    )
    result = run(Runner.run(Agent(name="A", model=model, tools=[lookup]), "x"))
    assert outputs(model.calls[1]) == [DEFAULT_TOOL_ERROR, DEFAULT_TOOL_ERROR]
    assert "hunter2" not in json.dumps(model.calls[1].input)
    assert result.final_output == "sorry"


def test_failure_error_function_none_reraises(run):
    @function_tool(failure_error_function=None)
    def boom() -> str:
        """Boom."""
        raise RuntimeError("boom")

    model = ScriptedModel([[function_call("boom", {}, "c1")]])
    with pytest.raises(RuntimeError, match="boom"):
        run(Runner.run(Agent(name="A", model=model, tools=[boom]), "x"))


def test_unknown_tool_raises_by_default_or_returns_error_when_configured(run):
    model = ScriptedModel([[function_call("ghost", {}, "g1")]])
    with pytest.raises(ModelBehaviorError, match="ghost"):
        run(Runner.run(Agent(name="A", model=model), "x"))

    model = ScriptedModel([[function_call("ghost", {}, "g1")], [assistant_message("ok")]])
    config = RunConfig(tool_not_found_behavior="return_error_to_model")
    run(Runner.run(Agent(name="A", model=model), "x", run_config=config))
    assert outputs(model.calls[1]) == ["Tool 'ghost' not found."]


def test_max_turns_counts_model_invocations(run):
    model = ScriptedModel([[function_call("add", {"a": 1, "b": 1}, f"c{n}")] for n in range(5)])
    with pytest.raises(MaxTurnsExceeded):
        run(Runner.run(Agent(name="A", model=model, tools=[add]), "loop", max_turns=2))
    assert len(model.calls) == 2


def test_stop_on_first_tool_skips_second_model_call(run):
    model = ScriptedModel([[function_call("add", {"a": 1, "b": 2}, "c1")]])
    agent = Agent(name="A", model=model, tools=[add], tool_use_behavior="stop_on_first_tool")
    assert run(Runner.run(agent, "x")).final_output == "3"
    assert len(model.calls) == 1


def test_tool_choice_reset_after_tool_use(run):
    model = ScriptedModel([[function_call("add", {"a": 1, "b": 2}, "c1")], [assistant_message("3")]])
    agent = Agent(name="A", model=model, tools=[add], model_settings={"tool_choice": "required"})
    run(Runner.run(agent, "x"))
    assert model.calls[0].settings.get("tool_choice") == "required"
    assert "tool_choice" not in model.calls[1].settings


def test_dynamic_instructions_are_resolved_every_turn(run):
    counter = {"n": 0}

    def instructions(ctx, agent):
        counter["n"] += 1
        return f"v{counter['n']} for {ctx.context['user']}"

    model = ScriptedModel([[function_call("add", {"a": 1, "b": 2}, "c1")], [assistant_message("3")]])
    agent = Agent(name="A", model=model, tools=[add], instructions=instructions)
    run(Runner.run(agent, "x", context={"user": "ada"}))
    assert [c.system_instructions for c in model.calls] == ["v1 for ada", "v2 for ada"]


def test_parallel_tool_calls_run_concurrently_and_keep_model_order(run):
    @function_tool
    async def slow(tag: str) -> str:
        """Slow."""
        await asyncio.sleep(0.3 if tag == "a" else 0.05)
        return f"done-{tag}"

    model = ScriptedModel(
        [[function_call("slow", {"tag": "a"}, "s1"), function_call("slow", {"tag": "b"}, "s2")], [assistant_message("ok")]]
    )
    start = time.perf_counter()
    run(Runner.run(Agent(name="A", model=model, tools=[slow]), "x"))
    assert time.perf_counter() - start < 0.33
    assert outputs(model.calls[1]) == ["done-a", "done-b"]


def test_structured_output_is_validated(run):
    def parse(text):
        data = json.loads(text)
        assert set(data) == {"city", "temp"}
        return data

    model = ScriptedModel([[assistant_message('{"city": "Paris", "temp": 21}')]])
    assert run(Runner.run(Agent(name="A", model=model, output_type=parse), "x")).final_output == {
        "city": "Paris",
        "temp": 21,
    }
    bad = ScriptedModel([[assistant_message("not json")]])
    with pytest.raises(ModelBehaviorError):
        run(Runner.run(Agent(name="A", model=bad, output_type=parse), "x"))


def test_context_object_is_never_sent_to_the_model(run):
    model = ScriptedModel([[assistant_message("ok")]])
    run(Runner.run(Agent(name="A", model=model), "hi", context={"api_token": "SECRET-123"}))
    assert "SECRET-123" not in json.dumps(model.calls[0].input)
