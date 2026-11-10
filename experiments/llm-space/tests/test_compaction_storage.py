"""Compaction writes a new message list. Run bodies leave the workspace file."""

from minispace.compaction import (
    apply_compaction,
    is_checkpoint,
    next_compact_path,
    plan_compaction,
    summarizer_prompt,
)
from minispace.storage import ORPHAN_RETENTION_MS, Home


def _turns() -> list[dict]:
    messages = []
    for index in range(1, 4):
        messages.append({"role": "user", "text": f"turn {index}"})
        messages.append({"role": "assistant", "text": f"reply {index}", "tool_calls": []})
    return messages


def test_plan_keeps_recent_user_turns_verbatim():
    messages = _turns()
    plan = plan_compaction(messages, 1)
    assert plan["turn_count"] == 3
    assert plan["kept_turn_count"] == 1
    assert plan["kept"][0]["text"] == "turn 3"
    assert [item["text"] for item in plan["to_summarize"]] == ["turn 1", "reply 1", "turn 2", "reply 2"]
    assert messages[0]["text"] == "turn 1"


def test_apply_builds_a_checkpoint_and_leaves_the_source():
    messages = _turns()
    before = [dict(item) for item in messages]
    applied = apply_compaction(plan_compaction(messages, 1), "Goal: keep going.")
    assert messages == before
    assert is_checkpoint(applied[0])
    assert applied[-2]["text"] == "turn 3"
    assert "Goal: keep going." in applied[0]["text"]


def test_recompaction_is_progressive_and_reuses_the_checkpoint_id():
    first = apply_compaction(plan_compaction(_turns(), 1), "checkpoint A")
    first[0]["id"] = "ckpt"
    extended = [
        *first,
        {"role": "user", "text": "turn 4"},
        {"role": "assistant", "text": "reply 4", "tool_calls": []},
    ]
    plan = plan_compaction(extended, 1)
    assert plan["previous"] == "checkpoint A"
    assert plan["checkpoint_id"] == "ckpt"
    assert plan["kept"][0]["text"] == "turn 4"
    prompt = summarizer_prompt(plan, "keep the path")
    assert "<previous-checkpoint>" in prompt
    assert "checkpoint A" in prompt
    assert "turn 4" not in prompt
    again = apply_compaction(plan, "checkpoint B")
    assert again[0]["id"] == "ckpt"
    assert "checkpoint B" in again[0]["text"]


def test_meta_message_stays_ahead_of_the_checkpoint():
    messages = [{"role": "user", "text": "runtime", "meta": True}, *_turns()]
    plan = plan_compaction(messages, 1)
    applied = apply_compaction(plan, "summary")
    assert applied[0]["meta"] is True
    assert is_checkpoint(applied[1])
    assert plan["turn_count"] == 3


def test_compact_names_continue_one_sequence():
    assert next_compact_path("notes/feature.json", set()) == "notes/feature-compact-1.json"
    existing = {"notes/feature-compact-1.json", "notes/feature-compact-3.json"}
    assert next_compact_path("notes/feature-compact-2.json", existing) == "notes/feature-compact-4.json"


def test_long_tool_output_is_truncated_in_the_summarizer_prompt():
    messages = [
        {"role": "user", "text": "go"},
        {
            "role": "assistant",
            "text": "",
            "tool_calls": [{"name": "bash", "output": "x" * 2500}],
        },
    ]
    prompt = summarizer_prompt(plan_compaction(messages, 0))
    assert "[truncated]" in prompt
    assert "x" * 2500 not in prompt


def test_run_bodies_leave_the_workspace_and_survive_delete():
    home = Home()
    home.save(
        "thread.json",
        {
            "system": "s",
            "messages": [{"role": "user", "text": "go"}],
            "runs": [{"id": "run-1", "thread": {"messages": ["body"]}}],
            "evaluations": [{"verdict": "tie"}],
        },
    )
    stored = home.workspace["thread.json"]
    assert "runs" not in stored
    assert stored["evaluations"][0]["verdict"] == "tie"
    ref = stored["run_index"][0]
    assert home.read_run("thread.json", ref)["thread"]["messages"] == ["body"]

    home.delete("thread.json", now=1_000)
    assert "thread.json" not in home.workspace
    assert home.read_run("thread.json", ref)["id"] == "run-1"
    assert home.maintain(1_000 + ORPHAN_RETENTION_MS) == []
    assert home.maintain(1_000 + ORPHAN_RETENTION_MS + 1) == ["thread.json"]
    assert "thread.json" not in home.history
