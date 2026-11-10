"""The borrowed loop stops after one model step. The workbench decides what follows."""

from minispace.model import ScriptedModel
from minispace.tools import Tool
from minispace.workbench import MAX_AUTO_TOOL_TURNS, Workbench


def _tools() -> list[Tool]:
    return [
        Tool("skill", "builtin"),
        Tool("bash", "builtin"),
        Tool("lookup", "function"),
        Tool("spawn_agent", "builtin", terminate=True),
        Tool("ask_user_question", "builtin", terminate=True),
        Tool("notes", "plugin"),
    ]


def _ok(_tool: Tool, arguments: dict, _runtime: str) -> str:
    return f"ok:{arguments}"


def test_step_mode_does_not_execute_tools():
    model = ScriptedModel(
        [{"tool_calls": [{"name": "skill", "arguments": {"name": "triage"}}]}]
    )
    bench = Workbench(model, _tools(), {"skill": _ok})
    result = bench.run(
        {"system": "s", "messages": [{"role": "user", "text": "go"}], "values": {}},
        mode="step",
    )
    assert result["stop"] == "step"
    assert result["model_calls"] == 1
    assert result["tool_executions"] == []
    assert result["pi_executed"] == []
    assert result["messages"][-1]["tool_calls"][0]["output"] is None
    assert model.script == []


def test_auto_executes_once_and_stops():
    model = ScriptedModel(
        [
            {"tool_calls": [{"name": "skill", "arguments": {"name": "triage"}}]},
            {"text": "should not run"},
        ]
    )
    bench = Workbench(model, _tools(), {"skill": _ok})
    result = bench.run(
        {"system": "s", "messages": [{"role": "user", "text": "go"}], "values": {}},
        mode="auto",
    )
    assert result["stop"] == "auto_once"
    assert result["model_calls"] == 1
    assert result["tool_executions"] == [{"name": "skill", "kind": "builtin", "is_error": False}]
    assert result["messages"][-1]["tool_calls"][0]["output"].startswith("ok:")
    assert model.script[0]["text"] == "should not run"


def test_react_loop_alternates_until_the_model_stops():
    model = ScriptedModel(
        [
            {"tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}]},
            {"text": "done"},
        ]
    )
    bench = Workbench(model, _tools(), {"bash": _ok})
    result = bench.run(
        {"system": "s", "messages": [{"role": "user", "text": "go"}], "values": {}},
        mode="react",
    )
    assert result["stop"] == "model_done"
    assert result["model_calls"] == 2
    assert [item["name"] for item in result["tool_executions"]] == ["bash"]
    assert result["pi_executed"] == []
    assert result["run_record"]["thread"]["messages"][-1]["text"] == "done"


def test_function_tool_stops_the_loop_before_any_execution():
    seen: list[str] = []

    def bash(tool: Tool, arguments: dict, runtime: str) -> str:
        seen.append(tool.name)
        return "nope"

    model = ScriptedModel(
        [
            {
                "tool_calls": [
                    {"name": "bash", "arguments": {"command": "ls"}},
                    {"name": "lookup", "arguments": {"q": "a"}},
                ]
            }
        ]
    )
    bench = Workbench(model, _tools(), {"bash": bash})
    result = bench.run(
        {"system": "s", "messages": [{"role": "user", "text": "go"}], "values": {}},
        mode="react",
    )
    assert result["stop"] == "human"
    assert seen == []
    assert result["messages"][-1]["tool_calls"][0]["output"] is None


def test_terminate_builtin_is_a_human_gate():
    model = ScriptedModel(
        [{"tool_calls": [{"name": "ask_user_question", "arguments": {"questions": []}}]}]
    )
    bench = Workbench(model, _tools(), {})
    result = bench.run(
        {"system": "s", "messages": [{"role": "user", "text": "go"}], "values": {}},
        mode="react",
    )
    assert result["stop"] == "human"
    assert result["tool_executions"] == []


def test_dangerous_bash_is_not_auto_executed():
    seen: list[str] = []

    def bash(_tool: Tool, arguments: dict, _runtime: str) -> str:
        seen.append(arguments["command"])
        return "ran"

    model = ScriptedModel(
        [{"tool_calls": [{"name": "bash", "arguments": {"command": "sudo rm -rf /tmp/x"}}]}]
    )
    bench = Workbench(model, _tools(), {"bash": bash})
    result = bench.run(
        {"system": "s", "messages": [{"role": "user", "text": "go"}], "values": {}},
        mode="react",
    )
    assert result["stop"] == "human"
    assert seen == []


def test_remote_plugin_becomes_an_error_result_and_is_not_invoked():
    seen: list[str] = []

    def notes(_tool: Tool, _arguments: dict, _runtime: str) -> str:
        seen.append("called")
        return "secret"

    model = ScriptedModel(
        [
            {"tool_calls": [{"name": "notes", "arguments": {}}]},
            {"text": "saw the error"},
        ]
    )
    bench = Workbench(model, _tools(), {"notes": notes})
    result = bench.run(
        {"system": "s", "messages": [{"role": "user", "text": "go"}], "values": {}},
        mode="react",
        runtime="remote",
    )
    assert seen == []
    assert result["tool_executions"][0]["is_error"] is True
    assert "local runtime" in result["messages"][1]["tool_calls"][0]["output"]
    assert result["stop"] == "model_done"


def test_react_loop_caps_model_turns():
    model = ScriptedModel(
        [{"tool_calls": [{"name": "skill", "arguments": {"name": "x"}}]}]
        * (MAX_AUTO_TOOL_TURNS + 3)
    )
    bench = Workbench(model, _tools(), {"skill": _ok})
    result = bench.run(
        {"system": "s", "messages": [{"role": "user", "text": "go"}], "values": {}},
        mode="react",
    )
    assert result["stop"] == "cap"
    assert result["model_calls"] == MAX_AUTO_TOOL_TURNS
    assert len(result["tool_executions"]) == MAX_AUTO_TOOL_TURNS
    assert len(model.script) == 3


def test_plain_assistant_reply_cannot_start_another_run():
    bench = Workbench(ScriptedModel([{"text": "hi"}]), _tools())
    try:
        bench.run(
            {
                "system": "s",
                "messages": [{"role": "assistant", "text": "already answered", "tool_calls": []}],
                "values": {},
            }
        )
    except RuntimeError as error:
        assert "tool call result" in str(error)
    else:
        raise AssertionError("expected a refusal")


def test_assistant_with_a_tool_call_can_be_continued():
    model = ScriptedModel([{"text": "after the tool"}])
    bench = Workbench(model, _tools())
    result = bench.run(
        {
            "system": "s",
            "messages": [
                {"role": "user", "text": "go"},
                {
                    "role": "assistant",
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "name": "lookup",
                            "arguments": {},
                            "output": "filled by hand",
                            "is_error": False,
                        }
                    ],
                },
            ],
            "values": {},
        },
        mode="step",
    )
    assert result["stop"] == "model_done"
    assert result["messages"][-1]["text"] == "after the tool"
