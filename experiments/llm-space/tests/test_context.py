"""Templates stay stored. A snapshot freezes what an earlier run already rendered."""

from minispace.model import ScriptedModel
from minispace.render import render_thread
from minispace.tools import Tool
from minispace.workbench import Workbench


def test_render_does_not_rewrite_the_stored_template():
    thread = {
        "system": "Date {{current_date}}",
        "messages": [{"role": "user", "text": "Hello {{current_date}}"}],
        "snapshot": {},
    }
    request, snapshot = render_thread(thread, {"current_date": "Monday"})
    assert thread["system"] == "Date {{current_date}}"
    assert thread["messages"][0]["text"] == "Hello {{current_date}}"
    assert request["system"] == "Date Monday"
    assert request["messages"][0]["text"] == "Hello Monday"
    assert snapshot["system"] == "Date Monday"
    assert snapshot["m0"] == "Hello Monday"


def test_snapshot_wins_when_the_value_moves():
    thread = {
        "system": "Date {{current_date}}",
        "messages": [
            {"role": "user", "text": "Hello {{current_date}}"},
            {"role": "assistant", "text": "ok", "tool_calls": []},
            {"role": "user", "text": "Now {{current_date}}"},
        ],
        "snapshot": {"system": "Date Monday", "m0": "Hello Monday"},
    }
    request, snapshot = render_thread(thread, {"current_date": "Tuesday"})
    assert request["system"] == "Date Monday"
    assert request["messages"][0]["text"] == "Hello Monday"
    assert request["messages"][2]["text"] == "Now Tuesday"
    assert snapshot["m2"] == "Now Tuesday"
    assert thread["messages"][2]["text"] == "Now {{current_date}}"


def test_workbench_attaches_the_snapshot_without_editing_the_template():
    model = ScriptedModel([{"text": "done"}])
    thread = {
        "system": "Date {{current_date}}",
        "messages": [{"role": "user", "text": "go"}],
        "values": {"current_date": "Monday"},
        "snapshot": {},
    }
    result = Workbench(model, [Tool("skill", "builtin")]).run(thread, mode="step")
    assert thread["system"] == "Date {{current_date}}"
    assert result["snapshot"]["system"] == "Date Monday"
    assert model.requests[0]["system"] == "Date Monday"
