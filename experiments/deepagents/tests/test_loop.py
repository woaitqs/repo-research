"""The loop: exit on no tool calls, one ToolMessage per call, onion-ordered middleware."""

from minideep import AIMessage, Agent, HumanMessage, Middleware, ScriptedModel, ToolCall, ToolMessage, tool


@tool
def add(a: int, b: int) -> str:
    """Add two numbers."""
    return str(a + b)


def test_loop_exits_when_model_stops_calling_tools():
    model = ScriptedModel(lambda m, t, n: AIMessage("hello"))
    state = Agent(model).invoke({"messages": [HumanMessage("hi")]})
    assert [type(m).__name__ for m in state["messages"]] == ["HumanMessage", "AIMessage"]
    assert len(model.calls) == 1


def test_parallel_tool_calls_each_get_a_result_then_loop_continues():
    def script(messages, tools, n):
        if n == 1:
            return AIMessage("", tool_calls=[ToolCall("add", {"a": 1, "b": 2}, "c1"), ToolCall("add", {"a": 3, "b": 4}, "c2")])
        return AIMessage("done")

    state = Agent(ScriptedModel(script), tools=[add]).invoke({"messages": [HumanMessage("sum")]})
    results = {m.tool_call_id: m.content for m in state["messages"] if isinstance(m, ToolMessage)}
    assert results == {"c1": "3", "c2": "7"}
    assert state["messages"][-1].content == "done"


def test_unknown_tool_and_tool_exception_become_error_observations():
    @tool
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("kaboom")

    def script(messages, tools, n):
        if n == 1:
            return AIMessage("", tool_calls=[ToolCall("nope", {}, "c1"), ToolCall("boom", {}, "c2")])
        return AIMessage("recovered")

    state = Agent(ScriptedModel(script), tools=[boom]).invoke({"messages": [HumanMessage("go")]})
    errors = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert all(m.status == "error" for m in errors)
    assert "not available" in errors[0].content and "kaboom" in errors[1].content
    assert state["messages"][-1].content == "recovered"


def test_wrap_model_call_is_onion_ordered_and_does_not_mutate_state():
    order = []

    class Tag(Middleware):
        def __init__(self, label):
            self.label = label

        @property
        def name(self):
            return self.label

        def wrap_model_call(self, request, handler):
            order.append(f"enter {self.label}")
            response = handler(request.override(system_prompt=request.system_prompt + f"[{self.label}]"))
            order.append(f"exit {self.label}")
            return response

    model = ScriptedModel(lambda m, t, n: AIMessage("ok"))
    state = Agent(model, system_prompt="base", middleware=[Tag("outer"), Tag("inner")]).invoke({"messages": [HumanMessage("x")]})
    assert order == ["enter outer", "enter inner", "exit inner", "exit outer"]
    assert model.calls[0].messages[0].content == "base[outer][inner]"  # per-call prompt edits compose
    assert all(m.content != "base[outer][inner]" for m in state["messages"])  # ...and never land in state
