import os
import subprocess
import sys

import pytest

from minipstack import dispatch, orch, sheet, skills
from minipstack.dispatch import Assignment, fan_out, interrogate, route


def test_parent_owns_the_route():
    assert route("claude", "claude:opus@max")[0] == "native"
    assert route("claude", "codex:gpt-6-luna@low")[0] == "external"
    assert route("codex", "inherit-parent")[0] == "native"


def test_a_child_assignment_carries_no_routing_inputs():
    assert set(Assignment.__dataclass_fields__) == {"label", "provider", "model", "effort", "mode", "prompt", "output_dir"}


def test_lanes_start_together_and_judging_waits_for_all_terminal(lab, tmp_path):
    lab.control(codex={"delay": 3.0, "text": "B"}, grok={"text": "C"})
    seen = {}

    def judge(results):
        external = [r for r in results if r.route == "external"]
        seen["terminal"] = len(results) == 3 and all(r.receipt["status"] == "complete" for r in external)
        seen["receipts"] = all(os.path.getsize(r.receipt["outputPath"]) > 0 for r in external)

    results = fan_out("claude", ["claude:opus@max", "codex:gpt-6-luna@low", "grok:grok-4.7@xhigh"], "task", str(tmp_path / "fan"),
                      native=lambda a: f"native {a.model}", cwd=lab.repo, source_env=lab.env, on_all_terminal=judge)
    starts = sorted(c["ts"] for c in lab.calls() if c["phase"] == "model")
    assert [r.answer for r in results] == ["native opus", "B", "C"] and all(r.counted for r in results)
    assert starts[-1] - starts[0] < 2.0  # run one after another, grok would start 3 s after codex
    assert seen == {"terminal": True, "receipts": True}


def test_a_failed_lane_is_a_named_dropout_never_a_substitute(lab, tmp_path):
    lab.control(codex={"exit": 1, "stderr": "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account."}, grok={})
    results = fan_out("claude", ["codex:gpt-6.1-sol@max", "grok:grok-4.7@xhigh"], "task", str(tmp_path / "fan"),
                      native=lambda a: "unused", cwd=lab.repo, source_env=lab.env)
    assert [(r.status, r.counted) for r in results] == [("unavailable-model", False), ("complete", True)]
    assert [c["argv"][c["argv"].index("--model") + 1] for c in lab.calls() if c["name"] == "codex" and c["phase"] == "model"] == ["gpt-6.1-sol"]


def test_interrogate_marks_findings_raised_by_two_models(lab, tmp_path):
    lab.control(codex={"text": "FINDING L3: divides by zero on empty input"}, grok={"text": "FINDING L3: empty list crashes\nFINDING L9: name"})
    verdict = interrogate("claude", ["codex:gpt-6-luna@low", "grok:grok-4.7@xhigh", "codex:gpt-6.1-sol@max"], "fix mean", "diff",
                          str(tmp_path / "rev"), native=lambda a: None, cwd=lab.repo, source_env=lab.env)
    assert [f["line"] for f in verdict["agreement"]] == [3] and [f["line"] for f in verdict["single"]] == [9]
    assert verdict["dropouts"] == []  # the fake accepts any model; a real refusal is covered above


def test_rolling_alias_pin_is_normalized_in_memory():
    parsed = sheet.parse("judgment and prose: claude:claude-opus-5-6@max\n")
    assert parsed.roles["judgment and prose"] == ["claude:opus@max"]
    assert parsed.migrations == [("claude:claude-opus-5-6@max", "claude:opus@max")]


def test_writer_role_on_a_read_only_provider_is_inconsistent():
    problems = sheet.validate(sheet.Sheet(roles={"feature, refactoring": ["devin:swe-2@max"]}))
    assert any("read-only lanes only" in p for p in problems)


def test_setup_writes_nothing_when_any_probe_fails(tmp_path):
    home = str(tmp_path)
    open(os.path.join(home, "CLAUDE.md"), "w").write("# mine\n")
    report = sheet.setup(home, probe=lambda pair: not pair.startswith("codex:"))
    assert report["written"] is False and report["reason"] == "probe failed"
    assert not os.path.exists(os.path.join(home, "pstack-models.md")) and open(os.path.join(home, "CLAUDE.md")).read() == "# mine\n"


def test_setup_writes_once_and_a_rerun_is_byte_identical(tmp_path):
    home = str(tmp_path)
    open(os.path.join(home, "CLAUDE.md"), "w").write("# mine\n")
    assert sheet.setup(home, probe=lambda pair: True)["written"] is True
    first = (open(os.path.join(home, "pstack-models.md")).read(), open(os.path.join(home, "CLAUDE.md")).read())
    again = sheet.setup(home, probe=lambda pair: True)
    assert again["unchanged"] is True
    assert (open(os.path.join(home, "pstack-models.md")).read(), open(os.path.join(home, "CLAUDE.md")).read()) == first
    assert first[1].count(sheet.IMPORT_LINE) == 1


def test_setup_restores_the_snapshot_when_readback_differs(tmp_path):
    home = str(tmp_path)
    open(os.path.join(home, "CLAUDE.md"), "w").write("# mine\n")
    corrupt = lambda path, data: open(path, "w").write(data[:-2])
    report = sheet.setup(home, probe=lambda pair: True, write=corrupt)
    assert report["written"] is False and "restored" in report["reason"]
    assert not os.path.exists(os.path.join(home, "pstack-models.md")) and open(os.path.join(home, "CLAUDE.md")).read() == "# mine\n"


def test_two_import_lines_stop_setup_instead_of_guessing():
    with pytest.raises(ValueError):
        sheet.wire_import("@./pstack-models.md\n@~/.claude/pstack-models.md\n")


def test_ledger_answers_only_for_the_exact_head(tmp_path):
    store = orch.Store(str(tmp_path / "store"))
    store.init()
    store.unit_set("u1", state="verify", pr="12", sha="aaa")
    store.ledger_record("12", "aaa", "unit-test-verified", "pytest 40 passed", "verifier-model")
    assert store.status()["verified"] == ["u1"]
    store.unit_set("u1", sha="bbb")  # a restack moved the head
    assert store.ledger_check("12", "bbb") == "unverified" and store.status()["verified"] == []


def test_store_lock_refuses_a_live_holder_and_takes_over_a_dead_one(tmp_path):
    store = orch.Store(str(tmp_path / "store"))
    store.init()
    lock = os.path.join(store.root, ".orch.lock")
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        open(lock, "w").write(f"{live.pid}\n")
        with pytest.raises(orch.StoreError, match="held by pid"):
            store.unit_set("u1", state="x")
    finally:
        live.kill()
        live.wait()
    store.unit_set("u1", state="x")  # the holder is dead now
    assert store.units()[0]["state"] == "x" and not os.path.exists(lock)


def test_inbox_is_a_queue_drained_in_batches(tmp_path):
    store = orch.Store(str(tmp_path / "store"))
    store.init()
    store.inbox_push("agent-1", "u1", "done")
    store.inbox_push("agent-2", "u2", "failed")
    assert [p["unit"] for p in store.inbox_drain()] == ["u1", "u2"] and store.inbox_drain() == []


def write_skill(root, name, front, body="body"):
    os.makedirs(os.path.join(root, name))
    open(os.path.join(root, name, "SKILL.md"), "w").write(f"---\nname: {name}\n{front}---\n{body}\n")


def test_context_holds_the_mandate_and_skill_names_not_bodies(tmp_path):
    root = str(tmp_path)
    write_skill(root, "poteto-mode", "description: entry point\n", "LONG BODY")
    write_skill(root, "principle-prove-it-works", "description: verify\nuser-invocable: false\n", "LEAF BODY")
    tree = skills.load_tree(root)
    context = skills.startup_context(tree, "You have pstack.")
    assert "pstack:poteto-mode: entry point" in context and "LONG BODY" not in context and "principle" not in context
    assert skills.invoke(tree, "pstack:poteto-mode").strip() == "LONG BODY"
    leaf = skills.principle_path(tree["poteto-mode"].path, "Prove It Works")
    assert open(leaf).read().endswith("LEAF BODY\n")
    with pytest.raises(KeyError):
        skills.invoke(tree, "pstack:principle-prove-it-works")


def test_the_mandate_fires_only_on_startup_clear_and_compact():
    assert [e for e in ("startup", "resume", "clear", "compact") if skills.session_start("m", e)] == ["startup", "clear", "compact"]
