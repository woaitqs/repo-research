"""A child thread is a paused sibling. An evaluation is a human record."""

import pytest

from minispace.evaluation import save_evaluation
from minispace.model import ScriptedModel
from minispace.subagent import build_subagent_thread
from minispace.tools import Tool


def _parent() -> dict:
    return {
        "system": "You are a general agent.",
        "messages": [
            {"role": "user", "text": "meta block", "meta": True},
            {"role": "user", "text": "Parent transcript that must stay here."},
            {"role": "assistant", "text": "working", "tool_calls": []},
        ],
        "values": {"current_date": "Monday"},
        "tools": [
            Tool("bash", "builtin"),
            Tool("skill", "builtin"),
            Tool("spawn_agent", "builtin", terminate=True),
            Tool("lookup", "function"),
        ],
    }


def test_child_keeps_system_and_meta_and_drops_the_transcript():
    child = build_subagent_thread(
        _parent(),
        parent_name="inbox",
        task_name="read logs",
        prompt="Read the log.",
        role="researcher",
    )
    assert child["executed"] is False
    assert child["system"] == "You are a general agent."
    assert child["messages"][0]["meta"] is True
    transcript = " ".join(message["text"] for message in child["messages"])
    assert "Parent transcript" not in transcript
    assert "Read the log." in child["messages"][-1]["text"]
    names = [tool.name for tool in child["tools"]]
    assert "spawn_agent" not in names
    assert "bash" not in names
    assert "skill" in names
    assert "lookup" in names


def test_code_reviewer_may_keep_bash_and_still_drops_spawn():
    child = build_subagent_thread(
        _parent(),
        parent_name="inbox",
        task_name="review",
        prompt="Review the diff.",
        role="code-reviewer",
    )
    names = [tool.name for tool in child["tools"]]
    assert "bash" in names
    assert "spawn_agent" not in names


def test_unknown_role_is_refused():
    with pytest.raises(ValueError):
        build_subagent_thread(
            _parent(),
            parent_name="inbox",
            task_name="nope",
            prompt="nope",
            role="writer",
        )


def test_evaluation_copies_the_rubric_and_does_not_call_a_model():
    model = ScriptedModel([{"text": "unused"}])
    thread: dict = {"evaluations": [], "model_calls": 0}
    rubric = {"id": "quality", "revision": 2, "criteria": ["clarity"]}
    record = save_evaluation(
        thread,
        left_run_id="run-a",
        right_run_id="run-b",
        rubric=rubric,
        scores={"run-a": {"clarity": 2}, "run-b": {"clarity": 5}},
        verdict="right",
    )
    rubric["revision"] = 9
    rubric["criteria"].append("tone")
    assert record["rubric"]["revision"] == 2
    assert record["rubric"]["criteria"] == ["clarity"]
    assert thread["model_calls"] == 0
    assert model.requests == []
    assert thread["evaluations"][0] is record


def test_evaluation_rejects_scores_outside_one_to_five():
    thread: dict = {}
    rubric = {"id": "quality", "revision": 1, "criteria": ["clarity"]}
    with pytest.raises(ValueError):
        save_evaluation(
            thread,
            left_run_id="a",
            right_run_id="b",
            rubric=rubric,
            scores={"a": {"clarity": 0}, "b": {"clarity": 5}},
            verdict="tie",
        )
