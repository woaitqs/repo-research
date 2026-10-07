"""Memory (AGENTS.md -> system prompt, loaded once per thread) and skills (progressive disclosure)."""

from minideep import AIMessage, HumanMessage, ScriptedModel, ToolCall, create_deep_agent
from minideep.graph import PatchToolCallsMiddleware


def test_memory_is_injected_into_every_system_prompt():
    model = ScriptedModel(lambda m, t, n: AIMessage("ok"))
    create_deep_agent(model, memory=["/AGENTS.md"]).invoke({"messages": [HumanMessage("hi")], "files": {"/AGENTS.md": "user likes python"}})
    system = model.calls[0].messages[0].content
    assert "<agent_memory>" in system and "user likes python" in system


def test_memory_written_mid_thread_is_stale_until_a_new_thread():
    """Reproduces upstream behavior verified against deepagents 0.7.22 (see README)."""

    def script(messages, tools, n):
        if n == 1:
            return AIMessage("", tool_calls=[ToolCall("edit_file", {"file_path": "/AGENTS.md", "old_string": "python", "new_string": "rust"}, "e1")])
        return AIMessage("ok")

    model = ScriptedModel(script)
    agent = create_deep_agent(model, memory=["/AGENTS.md"])
    thread = agent.invoke({"messages": [HumanMessage("I like rust now")], "files": {"/AGENTS.md": "user likes python"}})
    thread["messages"].append(HumanMessage("what do I like?"))
    thread = agent.invoke(thread)  # same thread: state (incl. memory_contents) carried over

    assert thread["files"]["/AGENTS.md"] == "user likes rust"
    assert "user likes python" in model.calls[-1].messages[0].content  # prompt still shows the old memory

    fresh = ScriptedModel(lambda m, t, n: AIMessage("ok"))
    create_deep_agent(fresh, memory=["/AGENTS.md"]).invoke({"messages": [HumanMessage("hi")], "files": thread["files"]})
    assert "user likes rust" in fresh.calls[0].messages[0].content  # new thread reloads


def test_skills_expose_only_metadata_until_read():
    skill = "---\nname: web-research\ndescription: Structured web research\n---\n# Steps\nSECRET-BODY-DETAILS"
    model = ScriptedModel(lambda m, t, n: AIMessage("ok"))
    create_deep_agent(model, skills=["/skills/"]).invoke({"messages": [HumanMessage("hi")], "files": {"/skills/web-research/SKILL.md": skill}})
    system = model.calls[0].messages[0].content
    assert "web-research" in system and "/skills/web-research/SKILL.md" in system
    assert "SECRET-BODY-DETAILS" not in system


def test_dangling_tool_calls_are_patched_before_the_next_run():
    interrupted = [HumanMessage("go"), AIMessage("", tool_calls=[ToolCall("execute", {"command": "sleep 999"}, "x1")])]
    update = PatchToolCallsMiddleware().before_agent({"messages": interrupted})
    patched = update["messages"].messages
    assert patched[-1].tool_call_id == "x1" and patched[-1].status == "error"
