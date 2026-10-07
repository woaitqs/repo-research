"""Agent.as_tool: a nested run with a generated brief; the parent keeps control."""

import json

from conftest import outputs

from miniagents import Agent, Runner, ScriptedModel, assistant_message, function_call


def test_sub_agent_sees_only_generated_input_and_parent_keeps_control(run):
    worker = Agent(name="Researcher", instructions="research", model=ScriptedModel([[assistant_message("42")]]))
    parent_model = ScriptedModel(
        [[function_call("research", {"input": "find the answer"}, "t1")], [assistant_message("It is 42.")]]
    )
    parent = Agent(name="Orchestrator", model=parent_model, tools=[worker.as_tool("research", "Research a question")])
    result = run(Runner.run(parent, "PARENT-ONLY-SECRET: what is the answer?"))

    child_call = worker.model.calls[0]
    assert child_call.input == [{"role": "user", "content": "find the answer"}]
    assert "PARENT-ONLY-SECRET" not in json.dumps(child_call.input)
    assert outputs(parent_model.calls[1]) == ["42"]
    assert result.last_agent is parent and result.final_output == "It is 42."


def test_sub_agent_shares_the_application_context_object(run):
    seen = []

    def instructions(ctx, agent):
        seen.append(ctx.context)
        return "child"

    worker = Agent(name="W", instructions=instructions, model=ScriptedModel([[assistant_message("ok")]]))
    parent = Agent(
        name="P",
        model=ScriptedModel([[function_call("w", {"input": "go"}, "t1")], [assistant_message("done")]]),
        tools=[worker.as_tool("w", "worker")],
    )
    app_state = {"tenant": "acme"}
    run(Runner.run(parent, "x", context=app_state))
    assert seen == [app_state] and seen[0] is app_state
