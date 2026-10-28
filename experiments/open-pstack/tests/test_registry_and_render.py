import os

import pytest

from minipstack import render
from minipstack.registry import UsageError, resolve_lane, resolve_model


def lane(**overrides):
    request = dict(parent="claude", provider="codex", model="gpt-6-luna", effort="low", mode="read-only",
                   prompt_path="/p", cwd="/c", output_path="/o", receipt_path="/r")
    request.update(overrides)
    return resolve_lane(**request)


def test_same_provider_lane_is_refused():
    with pytest.raises(UsageError, match="native to parent"):
        lane(parent="codex")


def test_versioned_pin_of_a_rolling_family_must_be_normalized_first():
    with pytest.raises(UsageError, match="normalize it to opus"):
        lane(parent="codex", provider="claude", model="claude-opus-5-6", effort="max")


def test_registered_exact_pin_is_not_normalized():
    assert resolve_model("claude", "claude-haiku-5-5")[0] == "current"


def test_effort_must_be_listed_for_the_family_or_kept_predecessor():
    with pytest.raises(UsageError, match="not offered"):
        lane(model="gpt-6-luna", effort="ultra")
    assert lane(model="gpt-5.6-sol", effort="ultra").family.name == "sol"


def test_unregistered_pair_runs_unchanged():
    assert lane(model="gpt-7-new", effort="high").cli_model == "gpt-7-new"


def test_model_id_provider_maps_effort_to_cli_model_and_refuses_writers():
    assert lane(provider="devin", model="swe-2", effort="high").cli_model == "swe-2-high"
    with pytest.raises(UsageError, match="does not support isolated-write"):
        lane(provider="devin", model="swe-2", effort="high", mode="isolated-write")


def test_full_access_is_only_a_writer_capability():
    with pytest.raises(UsageError, match="requires mode isolated-write"):
        lane(writer_sandbox="full-access")
    assert lane(mode="isolated-write", writer_sandbox="full-access").access.kind == "isolated-write-full-access"


def test_generated_files_round_trip_and_report_drift(tmp_path):
    root = str(tmp_path)
    render.write(root)
    assert render.drift(root) == []
    with open(os.path.join(root, render.CATALOG), "a") as f:
        f.write("hand edit\n")
    open(os.path.join(root, "agents", "pstack-stray-max.md"), "w").write("x")
    problems = render.drift(root)
    assert any("model-catalog.md differs" in p and "--write" in p for p in problems)
    assert any("stray agent" in p for p in problems)
    render.write(root)
    assert render.drift(root) == []


def test_one_native_agent_per_claude_family_and_effort():
    agents = render.render_agents()
    assert len(agents) == 15 and "agents/pstack-opus-max.md" in agents
    assert "disallowedTools: Agent, Task" in agents["agents/pstack-opus-max.md"]


def test_text_scan_rejects_descriptors_in_hand_written_markdown(tmp_path):
    root = str(tmp_path)
    render.write(root)
    os.makedirs(os.path.join(root, "skills", "arena"))
    with open(os.path.join(root, "skills", "arena", "SKILL.md"), "w") as f:
        f.write("Use `arena runners` from the sheet, or its role default.\n")
    assert render.text_scan(root) == []
    with open(os.path.join(root, "skills", "arena", "SKILL.md"), "a") as f:
        f.write("Default runner: claude:opus@max\nOr GPT 6.1 Sol by name.\n")  # one descriptor, one versioned name
    hits = render.text_scan(root)
    assert len(hits) == 2 and all(h.startswith("skills/arena/SKILL.md:") for h in hits)
