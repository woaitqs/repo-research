import json
import os
import stat
import threading
import time

import pytest

from minipstack.registry import UsageError
from minipstack.runner import run_lane


def test_provider_reported_model_proves_a_rolling_alias(lab):
    lab.control(claude={"reported": "claude-opus-5-5", "text": "OK"})
    code, receipt = run_lane(lab.lane("claude", parent="codex"), source_env=lab.env)
    assert (code, receipt["status"], receipt["modelEvidence"], receipt["reportedModel"]) == (0, "complete", "provider-report", "claude-opus-5-5")
    assert open(receipt["outputPath"]).read() == "OK"


def test_codex_is_proven_by_pinned_argv(lab):
    lab.control(codex={"text": "OK"})
    code, receipt = run_lane(lab.lane("codex", model="gpt-6-luna", effort="low"), source_env=lab.env)
    assert (code, receipt["modelEvidence"], receipt["reportedModel"]) == (0, "pinned-argv", None)
    argv = lab.calls()[-1]["argv"]
    assert argv[argv.index("--sandbox") + 1] == "read-only" and "--ephemeral" in argv


def test_a_different_served_model_is_malformed_and_leaves_no_answer(lab):
    lab.control(claude={"reported": "claude-sonnet-5-5"})
    code, receipt = run_lane(lab.lane("claude", parent="codex"), source_env=lab.env)
    assert (code, receipt["status"]) == (65, "malformed-output")
    assert not os.path.exists(receipt["outputPath"])


def test_an_unlisted_model_is_a_dropout_and_the_model_never_runs(lab):
    lab.control(grok={"models": ["grok-4.6"]})
    code, receipt = run_lane(lab.lane("grok", model="grok-4.7", effort="xhigh"), source_env=lab.env)
    assert (code, receipt["status"]) == (69, "unavailable-model")
    assert [c["phase"] for c in lab.calls()] == ["preflight"]


def test_allowlist_environment_drops_identity_and_unlisted_secrets(lab):
    lab.control(grok={})
    env = {**lab.env, "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "OPENAI_API_KEY": "k", "XAI_API_KEY": "x", "GIT_DIR": "/g"}
    run_lane(lab.lane("grok", model="grok-4.7", effort="xhigh"), source_env=env)
    seen = lab.calls()[-1]["env"]
    assert "XAI_API_KEY" in seen and not {"CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "OPENAI_API_KEY", "GIT_DIR"} & set(seen)


def test_inherited_environment_drops_only_identity_and_writers_lose_git_variables(lab):
    lab.control(claude={})
    env = {**lab.env, "CODEX_THREAD_ID": "t", "OPENAI_API_KEY": "k", "GIT_DIR": "/g"}
    run_lane(lab.lane("claude", parent="codex"), source_env=env)
    reader = lab.calls()[-1]["env"]
    run_lane(lab.lane("claude", parent="codex", mode="isolated-write", cwd=lab.worktree), source_env=env)
    writer = lab.calls()[-1]["env"]
    assert "CODEX_THREAD_ID" not in reader and "OPENAI_API_KEY" in reader and "GIT_DIR" in reader
    assert "GIT_DIR" not in writer


def test_existing_receipt_is_never_overwritten_and_reserved_files_are_private(lab):
    lab.control(claude={})
    lane = lab.lane("claude", parent="codex")
    open(lane.receipt_path, "w").write("previous")
    with pytest.raises(UsageError):
        run_lane(lane, source_env=lab.env)
    assert open(lane.receipt_path).read() == "previous" and not os.path.exists(lane.output_path)
    fresh = lab.lane("claude", parent="codex")
    run_lane(fresh, source_env=lab.env)
    assert {stat.S_IMODE(os.stat(p).st_mode) for p in (fresh.output_path, fresh.receipt_path, fresh.receipt_path + ".stdout")} == {0o600}


def test_preflight_retries_once_only_after_unauthenticated(lab):
    lab.control(grok={"preflight": "logged-out-once"})
    code, receipt = run_lane(lab.lane("grok", model="grok-4.7", effort="xhigh"), source_env=lab.env, retry_delay_s=0.2)
    assert code == 0 and [c["phase"] for c in lab.calls()] == ["preflight", "preflight", "model"]
    assert "attempt 1 failed" in receipt["preflight"]["evidence"]


def test_unauthenticated_comes_only_from_the_preflight(lab):
    lab.control(claude={"exit": 1, "stderr": "Error: authentication failed"})
    code, receipt = run_lane(lab.lane("claude", parent="codex"), source_env=lab.env)
    assert (code, receipt["status"]) == (70, "child-failed")


def test_one_absolute_deadline_spans_preflight_and_model(lab):
    lab.control(claude={"preflight_delay": 1.5, "delay": 10})
    started = time.time()
    code, receipt = run_lane(lab.lane("claude", parent="codex", timeout_s=3.0), source_env=lab.env, started=started)
    assert (code, receipt["status"], receipt["preflight"]["status"]) == (124, "timed-out", "passed")
    assert receipt["elapsedMs"] < 4000  # a fresh allowance per phase ends after preflight + 3 s, past 4.5 s


def test_cancellation_reaches_the_child_and_keeps_partial_output(lab):
    marker = os.path.join(lab.root, "started")
    lab.control(claude={"started": marker, "partial": "partial-bytes", "delay": 30})
    lane = lab.lane("claude", parent="codex")
    cancel = threading.Event()

    def cancel_when_started():
        while not os.path.exists(marker):
            time.sleep(0.02)
        time.sleep(0.2)
        cancel.set()

    threading.Thread(target=cancel_when_started, daemon=True).start()
    code, receipt = run_lane(lane, source_env=lab.env, cancel=cancel)
    assert (code, receipt["status"], receipt["signal"]) == (130, "cancelled", "SIGTERM")
    assert not os.path.exists(lane.output_path) and open(lane.receipt_path + ".stdout").read() == "partial-bytes"


def test_claude_writer_denies_the_shared_git_directory(lab):
    lab.control(claude={})
    run_lane(lab.lane("claude", parent="codex", mode="isolated-write", cwd=lab.worktree), source_env=lab.env)
    argv = lab.calls()[-1]["argv"]
    sandbox = json.loads(argv[argv.index("--settings") + 1])["sandbox"]
    assert sandbox["filesystem"]["denyWrite"] == [os.path.realpath(os.path.join(lab.repo, ".git"))]
    assert sandbox["allowUnsandboxedCommands"] is False and argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_writer_inside_the_shared_git_directory_is_refused_before_launch(lab):
    lab.control(claude={})
    inside = os.path.join(lab.repo, ".git", "inside")
    os.makedirs(inside)
    code, receipt = run_lane(lab.lane("claude", parent="codex", mode="isolated-write", cwd=inside), source_env=lab.env)
    assert (code, receipt["status"]) == (70, "child-failed") and lab.calls() == []


def test_exit_zero_with_unparseable_output_is_malformed(lab):
    lab.control(claude={"raw": "not json"})
    code, receipt = run_lane(lab.lane("claude", parent="codex"), source_env=lab.env)
    assert (code, receipt["status"]) == (65, "malformed-output") and not os.path.exists(receipt["outputPath"])
