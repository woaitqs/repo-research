"""Capabilities prepare a per-run execution agent around a workspace (SandboxAgent analogue)."""

from conftest import outputs

from miniagents import (
    CapableAgent,
    Compaction,
    Memory,
    RunConfig,
    Runner,
    ScriptedModel,
    Shell,
    Skills,
    Workspace,
    assistant_message,
    function_call,
)


def _agent(model):
    return CapableAgent(
        name="Analyst",
        instructions="Answer from the workspace.",
        model=model,
        files={"notes.md": "launch: 2026-11-02\n", "memories/memory_summary.md": "- user prefers terse answers\n"},
        capabilities=[
            Shell(),
            Skills({"release-notes": ("Write release notes.", "SKILL-BODY-STEP-1")}),
            Memory(),
            Compaction(threshold=1000),
        ],
    )


def test_execution_agent_gets_tools_prompt_and_sampling_params(run):
    model = ScriptedModel([[function_call("exec_command", {"cmd": "cat notes.md"}, "e1")], [assistant_message("2026-11-02")]])
    agent = _agent(model)
    ws = Workspace()
    try:
        result = run(Runner.run(agent, "When do we launch?", run_config=RunConfig(workspace=ws)))
    finally:
        ws.close()
    first = model.calls[0]
    system = first.system_instructions
    assert first.tools == ["exec_command"]
    assert system.index("# Agent instructions") < system.index("# Capability instructions") < system.index("# Filesystem")
    assert "- release-notes: Write release notes. (file: .agents/release-notes/SKILL.md)" in system
    assert "SKILL-BODY-STEP-1" not in system  # Index in prompt, body on disk.
    assert "user prefers terse answers" in system
    assert first.settings["context_management"] == [{"type": "compaction", "compact_threshold": 1000}]
    assert "launch: 2026-11-02" in outputs(model.calls[1])[0]
    # The public agent is untouched and is what the result reports.
    assert result.last_agent is agent and agent.tools == [] and agent.model_settings == {}


def test_capabilities_are_cloned_per_run(run):
    agent = _agent(ScriptedModel([[assistant_message("a")], [assistant_message("b")]]))
    ws1, ws2 = Workspace(), Workspace()
    try:
        run(Runner.run(agent, "x", run_config=RunConfig(workspace=ws1)))
        run(Runner.run(agent, "y", run_config=RunConfig(workspace=ws2)))
        assert all(c.workspace is None for c in agent.capabilities)
    finally:
        ws1.close()
        ws2.close()


def test_skill_body_is_materialized_in_the_workspace(run):
    model = ScriptedModel(
        [[function_call("exec_command", {"cmd": "cat .agents/release-notes/SKILL.md"}, "e1")], [assistant_message("ok")]]
    )
    ws = Workspace()
    try:
        run(Runner.run(_agent(model), "write release notes", run_config=RunConfig(workspace=ws)))
    finally:
        ws.close()
    assert "SKILL-BODY-STEP-1" in outputs(model.calls[1])[0]


def test_compaction_drops_history_before_latest_compaction_item(run):
    model = ScriptedModel([[assistant_message("ok")]])
    history = [
        {"role": "user", "content": "very old"},
        {"type": "compaction", "id": "c1", "encrypted_content": "..."},
        {"role": "user", "content": "new"},
    ]
    ws = Workspace()
    try:
        run(Runner.run(_agent(model), history, run_config=RunConfig(workspace=ws)))
    finally:
        ws.close()
    assert [i.get("type") or i.get("role") for i in model.calls[0].input] == ["compaction", "user"]
