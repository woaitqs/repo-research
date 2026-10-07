"""Sub-agents: isolated context in, final text out, shared files, no recursion."""

from minideep import AIMessage, HumanMessage, ScriptedModel, SubAgentSpec, ToolCall, ToolMessage, create_deep_agent


def _delegating_parent(subagent_type="writer"):
    def script(messages, tools, n):
        if n == 1:
            return AIMessage("", tool_calls=[ToolCall("task", {"description": "Write /report.md", "subagent_type": subagent_type}, "t1")])
        return AIMessage("parent done")

    return script


def _writer_script(messages, tools, n):
    if n == 1:
        return AIMessage("", tool_calls=[ToolCall("write_file", {"file_path": "/report.md", "content": "# Report"}, "w1")])
    if n == 2:
        return AIMessage("", tool_calls=[ToolCall("read_file", {"file_path": "/report.md"}, "r1")])
    return AIMessage("Report written to /report.md")


def test_subagent_sees_only_the_task_description_and_parent_sees_only_its_final_text():
    parent, writer = ScriptedModel(_delegating_parent()), ScriptedModel(_writer_script)
    agent = create_deep_agent(parent, subagents=[SubAgentSpec("writer", "Writes reports", "You write reports.", model=writer)])
    state = agent.invoke({"messages": [HumanMessage("a long user conversation " * 50)]})

    first_child_request = writer.calls[0].messages
    assert [m.role for m in first_child_request] == ["system", "human"]
    assert first_child_request[1].content == "Write /report.md"  # no parent history leaked

    task_result = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "t1")
    assert task_result.content == "Report written to /report.md"
    # The child's 5 internal messages never enter the parent transcript:
    assert len(state["messages"]) == 4  # human, ai(task), tool(result), ai(final)


def test_files_written_by_subagent_flow_back_through_state():
    parent, writer = ScriptedModel(_delegating_parent()), ScriptedModel(_writer_script)
    agent = create_deep_agent(parent, subagents=[SubAgentSpec("writer", "Writes reports", model=writer)])
    state = agent.invoke({"messages": [HumanMessage("go")], "files": {"/notes.md": "parent note"}})
    assert state["files"] == {"/notes.md": "parent note", "/report.md": "# Report"}


def test_subagents_cannot_delegate_further_and_unknown_types_are_reported():
    writer = ScriptedModel(_writer_script)
    agent = create_deep_agent(ScriptedModel(_delegating_parent("nope")), subagents=[SubAgentSpec("writer", "w", model=writer)])
    state = agent.invoke({"messages": [HumanMessage("go")]})
    result = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert "does not exist" in result.content and "`general-purpose`" in result.content

    seen = {}

    def child(messages, tools, n):
        seen["tools"] = tools
        return AIMessage("child done")

    create_deep_agent(ScriptedModel(_delegating_parent()), subagents=[SubAgentSpec("writer", "w", model=ScriptedModel(child))]).invoke(
        {"messages": [HumanMessage("go")]}
    )
    assert "task" not in seen["tools"]


def test_private_state_is_not_shared_with_subagents():
    seen = {}
    writer = ScriptedModel(lambda m, t, n: AIMessage("child done"))
    agent = create_deep_agent(ScriptedModel(_delegating_parent()), subagents=[SubAgentSpec("writer", "w", model=writer)], memory=["/AGENTS.md"])
    original_invoke = agent.middleware[1].agents["writer"].invoke

    def spy_invoke(child_state):
        seen["keys"] = set(child_state)
        return original_invoke(child_state)

    agent.middleware[1].agents["writer"].invoke = spy_invoke
    agent.invoke({"messages": [HumanMessage("go")], "files": {"/AGENTS.md": "secret prefs"}})
    assert "memory_contents" not in seen["keys"]
    assert "files" in seen["keys"]
