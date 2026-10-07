"""FilesystemMiddleware: capability-gated tools, large-result offload, mutation guard."""

from minideep import (
    AIMessage,
    CompositeBackend,
    HumanMessage,
    ScriptedModel,
    ShellBackend,
    StateBackend,
    ToolCall,
    ToolMessage,
    create_deep_agent,
    tool,
)


@tool
def dump(n: int) -> str:
    """Return n lines of output."""
    return "\n".join(f"row {i} " + "x" * 50 for i in range(n))


def _tool_results(state):
    return [m for m in state["messages"] if isinstance(m, ToolMessage)]


def test_execute_is_hidden_without_a_shell_and_visible_with_one(tmp_path):
    seen = {}

    def script(messages, tools, n):
        seen.setdefault("tools", tools)
        return AIMessage("ok")

    create_deep_agent(ScriptedModel(script), backend=StateBackend()).invoke({"messages": [HumanMessage("x")]})
    assert "execute" not in seen.pop("tools")

    create_deep_agent(ScriptedModel(script), backend=ShellBackend(tmp_path)).invoke({"messages": [HumanMessage("x")]})
    assert "execute" in seen["tools"]


def test_large_tool_result_is_offloaded_and_paged_back_in():
    def script(messages, tools, n):
        if n == 1:
            return AIMessage("", tool_calls=[ToolCall("dump", {"n": 2000}, "big1")])
        if n == 2:
            return AIMessage("", tool_calls=[ToolCall("read_file", {"file_path": "/large_tool_results/big1", "offset": 1000, "limit": 3}, "r1")])
        return AIMessage("done")

    model = ScriptedModel(script)
    agent = create_deep_agent(model, tools=[dump], tool_token_limit_before_evict=1_000)
    state = agent.invoke({"messages": [HumanMessage("dump it")]})

    stub, page = _tool_results(state)
    assert "/large_tool_results/big1" in state["files"]
    assert len(state["files"]["/large_tool_results/big1"].splitlines()) == 2000  # full result kept
    assert stub.content.startswith("Tool result too large")
    assert "[1990 lines truncated]" in stub.content  # head 5 + tail 5 preview
    assert len(stub.content) < 2_000  # the model sees a stub, not 100k chars
    assert "row 1000 " in page.content and "1001\t" in page.content  # paging works with line numbers


def test_read_file_results_are_never_offloaded():
    def script(messages, tools, n):
        if n == 1:
            return AIMessage("", tool_calls=[ToolCall("read_file", {"file_path": "/big.txt", "limit": 5000}, "r1")])
        return AIMessage("done")

    big = "\n".join("y" * 60 for _ in range(3000))
    agent = create_deep_agent(ScriptedModel(script), tool_token_limit_before_evict=1_000)
    state = agent.invoke({"messages": [HumanMessage("read")], "files": {"/big.txt": big}})
    assert not any(p.startswith("/large_tool_results/") for p in state["files"])


def test_parallel_mutations_to_same_path_are_rejected():
    def script(messages, tools, n):
        if n == 1:
            return AIMessage(
                "",
                tool_calls=[
                    ToolCall("write_file", {"file_path": "/a.txt", "content": "one"}, "w1"),
                    ToolCall("write_file", {"file_path": "/a.txt", "content": "two"}, "w2"),
                ],
            )
        return AIMessage("done")

    state = create_deep_agent(ScriptedModel(script)).invoke({"messages": [HumanMessage("write")]})
    first, second = _tool_results(state)
    assert first.status == "success" and second.status == "error"
    assert state["files"]["/a.txt"] == "one"


def test_composite_backend_routes_by_longest_prefix_and_offloads_under_artifacts_root(tmp_path):
    backend = CompositeBackend(default=ShellBackend(tmp_path), routes={"/memories/": StateBackend()}, artifacts_root="/.artifacts")

    def script(messages, tools, n):
        if n == 1:
            return AIMessage(
                "",
                tool_calls=[
                    ToolCall("write_file", {"file_path": "/memories/prefs.md", "content": "likes rust"}, "w1"),
                    ToolCall("write_file", {"file_path": "/src/app.py", "content": "print(1)"}, "w2"),
                    ToolCall("execute", {"command": "python3 -c \"print('z' * 9000)\""}, "e1"),
                ],
            )
        return AIMessage("done")

    state = create_deep_agent(ScriptedModel(script), backend=backend, tool_token_limit_before_evict=1_000).invoke(
        {"messages": [HumanMessage("go")]}
    )
    assert state["files"] == {"/prefs.md": "likes rust"}  # routed into state, prefix stripped
    assert (tmp_path / "src" / "app.py").read_text() == "print(1)"  # default route -> disk
    assert (tmp_path / ".artifacts" / "large_tool_results" / "e1").exists()  # execute output offloaded
