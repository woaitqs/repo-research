"""Input guardrails: first agent, first turn, blocking or racing. Output guardrails: final only."""

import asyncio

import pytest

from miniagents import (
    Agent,
    GuardrailOutput,
    InputGuardrail,
    InputGuardrailTripwireTriggered,
    OutputGuardrail,
    OutputGuardrailTripwireTriggered,
    Runner,
    ScriptedModel,
    assistant_message,
    function_call,
    function_tool,
)


async def _trip(ctx, agent, items):
    return GuardrailOutput(True, "blocked")


def test_blocking_guardrail_prevents_the_model_call(run):
    model = ScriptedModel([[assistant_message("never")]])
    agent = Agent(name="A", model=model, input_guardrails=[InputGuardrail(_trip, run_in_parallel=False)])
    with pytest.raises(InputGuardrailTripwireTriggered):
        run(Runner.run(agent, "hi"))
    assert model.calls == []


def test_slow_parallel_guardrail_can_lose_the_race_to_side_effects(run):
    executed = []

    @function_tool
    def send_email(to: str) -> str:
        """Send an email."""
        executed.append(to)
        return "sent"

    async def slow_trip(ctx, agent, items):
        await asyncio.sleep(0.2)
        return GuardrailOutput(True, "blocked")

    model = ScriptedModel([[function_call("send_email", {"to": "a@b.c"}, "e1")], [assistant_message("done")]])
    agent = Agent(name="A", model=model, tools=[send_email], input_guardrails=[InputGuardrail(slow_trip)])
    with pytest.raises(InputGuardrailTripwireTriggered):
        run(Runner.run(agent, "email"))
    assert executed == ["a@b.c"]  # Same trade-off upstream documents in docs/guardrails.md.


def test_guardrails_of_handoff_target_do_not_run(run):
    calls = []

    async def record(ctx, agent, items):
        calls.append(agent.name)
        return GuardrailOutput(False)

    child = Agent(name="Child", model=ScriptedModel([[assistant_message("ok")]]), input_guardrails=[InputGuardrail(record)])
    parent = Agent(
        name="Parent",
        model=ScriptedModel([[function_call("transfer_to_child", {}, "h1")]]),
        handoffs=[child],
        input_guardrails=[InputGuardrail(record)],
    )
    run(Runner.run(parent, "x"))
    assert calls == ["Parent"]


def test_output_guardrail_checks_the_final_output(run):
    async def no_secrets(ctx, agent, output):
        return GuardrailOutput("secret" in output, "leak")

    agent = Agent(name="A", model=ScriptedModel([[assistant_message("the secret is 7")]]), output_guardrails=[OutputGuardrail(no_secrets)])
    with pytest.raises(OutputGuardrailTripwireTriggered):
        run(Runner.run(agent, "x"))
