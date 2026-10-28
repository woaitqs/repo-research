"""Probe the real `pstack-runner` from an open-pstack clone with scripted provider CLIs.

Each probe launches the shipped launcher (`plugins/pstack/skills/poteto-mode/scripts/runner/pstack-runner`,
which execs Bun) exactly as a parent harness would: an argv array, a prompt file, a cwd, and
fresh output and receipt paths. The provider CLIs on PATH are `fake_cli.py` wrappers, so no
network or login is involved. Each probe states a claim from the study, observes the receipt,
the exit status and what the fake CLI recorded, and says whether the claim held.

    python3 probe_runner.py /path/to/open-pstack [--out probe_output.json]

Needs bun and git on PATH. The clone should be at b4c5e3d (Open Pstack 1.16.0).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_cli.py")
RUNNER_REL = "plugins/pstack/skills/poteto-mode/scripts/runner/pstack-runner"


class Lab:
    """A private bin directory of fake CLIs, a scratch git repository and output space."""

    def __init__(self, clone: str):
        self.clone = os.path.abspath(clone)
        self.runner = os.path.join(self.clone, RUNNER_REL)
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="pstack-probe-"))
        self.bin = os.path.join(self.root, "bin")
        os.mkdir(self.bin)
        for name in ("claude", "codex", "grok", "devin"):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                # A Python shebang, not /bin/sh, so the recorded environment is exactly what the runner passed.
                f.write(f"#!{sys.executable} -I\nimport runpy, sys\n"
                        f"sys.argv = [{FAKE!r}, {self.bin!r}, {name!r}, *sys.argv[1:]]\n"
                        f"runpy.run_path({FAKE!r}, run_name='__main__')\n")
            os.chmod(path, 0o755)
        self.repo = os.path.join(self.root, "repo")
        os.mkdir(self.repo)
        self._git("init", "-q", "--initial-branch=main")
        self._git("config", "user.name", "probe")
        self._git("config", "user.email", "probe@example.com")
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write("probe\n")
        self._git("add", ".")
        self._git("commit", "-qm", "init")
        self.worktree = os.path.join(self.root, "wt")
        self._git("worktree", "add", "-q", self.worktree, "-b", "lane")
        self.home = os.path.join(self.root, "home")
        os.mkdir(self.home)
        bun_dir = os.path.dirname(shutil.which("bun") or "")
        git_dir = os.path.dirname(shutil.which("git") or "/usr/bin/git")
        self.path = os.pathsep.join(dict.fromkeys([self.bin, bun_dir, git_dir, "/usr/bin", "/bin"]))
        self.n = 0

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", self.repo, *args], check=True, capture_output=True, text=True).stdout

    def control(self, value: dict) -> None:
        for leftover in os.listdir(self.bin):
            if leftover.endswith(".count") or leftover == "calls.jsonl":
                os.unlink(os.path.join(self.bin, leftover))
        with open(os.path.join(self.bin, "control.json"), "w") as f:
            json.dump(value, f)

    def calls(self) -> list[dict]:
        path = os.path.join(self.bin, "calls.jsonl")
        return [json.loads(line) for line in open(path)] if os.path.exists(path) else []

    def lane(self, provider: str, *, parent: str = "claude", model: str = "m", effort: str = "low",
             mode: str = "read-only", cwd: str | None = None, extra: list[str] | None = None,
             prompt: str = "probe prompt") -> dict:
        self.n += 1
        out = os.path.join(self.root, "out", f"{self.n:02d}")
        os.makedirs(out)
        prompt_path = os.path.join(out, "prompt.md")
        with open(prompt_path, "w") as f:
            f.write(prompt)
        return {
            "argv": [self.runner, "--parent", parent, "--provider", provider, "--model", model,
                     "--effort", effort, "--mode", mode, "--prompt", prompt_path,
                     "--cwd", cwd or self.repo, "--output", os.path.join(out, "answer.md"),
                     "--receipt", os.path.join(out, "receipt.json"), *(extra or [])],
            "output": os.path.join(out, "answer.md"),
            "receipt": os.path.join(out, "receipt.json"),
            "dir": out,
        }

    def env(self, extra: dict | None = None) -> dict:
        base = {"PATH": self.path, "HOME": self.home, "USER": "probe", "LANG": "C", "TMPDIR": self.root}
        base.update(extra or {})
        return base

    def run(self, lane: dict, extra_env: dict | None = None, *, signal_after_marker: str | None = None) -> dict:
        started = time.time()
        proc = subprocess.Popen(lane["argv"], env=self.env(extra_env), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        sent = None
        if signal_after_marker is not None:
            deadline = time.time() + 20
            while not os.path.exists(signal_after_marker) and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.2)
            proc.send_signal(signal.SIGTERM)
            sent = "SIGTERM"
        stdout, stderr = proc.communicate(timeout=120)
        receipt = None
        if os.path.exists(lane["receipt"]) and os.path.getsize(lane["receipt"]) > 0:
            try:
                receipt = json.load(open(lane["receipt"]))
            except json.JSONDecodeError:
                receipt = None
        return {"exit": proc.returncode, "stdout": stdout, "stderr": stderr, "receipt": receipt,
                "elapsed_s": round(time.time() - started, 2), "signal_sent": sent}

    def scrub(self, value):
        text = json.dumps(value)
        text = text.replace(self.root, "<lab>").replace(self.clone, "<clone>")
        text = text.replace(os.path.expanduser("~"), "<home>")
        return json.loads(text)


def mode_of(path: str) -> str:
    return oct(stat.S_IMODE(os.stat(path).st_mode))


PROBES = []


def probe(pid: str, claim: str):
    def wrap(fn):
        PROBES.append((pid, claim, fn))
        return fn
    return wrap


@probe("P1", "a same-provider lane is refused before anything is reserved (the native route is the parent's)")
def p1(lab: Lab):
    lab.control({})
    lane = lab.lane("claude", parent="claude", model="opus")
    r = lab.run(lane)
    held = r["exit"] == 64 and "native to parent claude" in r["stderr"] and not os.path.exists(lane["receipt"]) \
        and not lab.calls()
    return held, {"exit": r["exit"], "stderr_head": r["stderr"].splitlines()[0], "receipt_exists": os.path.exists(lane["receipt"]),
                  "cli_calls": len(lab.calls())}


@probe("P2", "an allowlist provider (grok) gets only the allowlist plus GROK_/XAI_ variables; parent identity markers and other secrets are dropped")
def p2(lab: Lab):
    lab.control({"grok": {}})
    lane = lab.lane("grok", model="grok-4.7")
    r = lab.run(lane, {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "parent-session", "OPENAI_API_KEY": "sk-probe",
                       "AWS_SECRET_ACCESS_KEY": "probe", "GIT_DIR": "/elsewhere", "XAI_API_KEY": "xai-probe",
                       "GROK_PROBE": "1", "LC_ALL": "C"})
    env = lab.calls()[-1]["env"]
    kept = sorted(env)
    dropped = [k for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GIT_DIR") if k in env]
    held = r["exit"] == 0 and not dropped and {"XAI_API_KEY", "GROK_PROBE", "LC_ALL", "PATH", "HOME"} <= set(env)
    return held, {"exit": r["exit"], "status": r["receipt"]["status"], "child_env_names": kept, "leaked": dropped}


@probe("P3", "an inherit provider (claude, from a Codex parent) keeps the environment but loses the Codex identity markers; an isolated-write lane also loses GIT_*")
def p3(lab: Lab):
    lab.control({"claude": {"reported_model": "claude-haiku-5-5"}})
    parent_env = {"CODEX_THREAD_ID": "t", "CODEX_SANDBOX": "seatbelt", "OPENAI_API_KEY": "sk-probe", "GIT_DIR": "/elsewhere",
                  "GIT_AUTHOR_NAME": "probe"}
    ro = lab.run(lab.lane("claude", parent="codex", model="claude-haiku-5-5"), parent_env)
    ro_env = lab.calls()[-1]["env"]
    lab.control({"claude": {"reported_model": "claude-haiku-5-5"}})
    wr = lab.run(lab.lane("claude", parent="codex", model="claude-haiku-5-5", mode="isolated-write", cwd=lab.worktree), parent_env)
    wr_env = lab.calls()[-1]["env"]
    observed = {
        "read_only": {"exit": ro["exit"], "CODEX_THREAD_ID": "CODEX_THREAD_ID" in ro_env, "CODEX_SANDBOX": "CODEX_SANDBOX" in ro_env,
                      "OPENAI_API_KEY": "OPENAI_API_KEY" in ro_env, "GIT_DIR": "GIT_DIR" in ro_env},
        "isolated_write": {"exit": wr["exit"], "CODEX_THREAD_ID": "CODEX_THREAD_ID" in wr_env,
                           "GIT_DIR": "GIT_DIR" in wr_env, "GIT_AUTHOR_NAME": "GIT_AUTHOR_NAME" in wr_env},
    }
    held = (ro["exit"] == 0 and wr["exit"] == 0 and not observed["read_only"]["CODEX_THREAD_ID"]
            and not observed["read_only"]["CODEX_SANDBOX"] and observed["read_only"]["OPENAI_API_KEY"]
            and observed["read_only"]["GIT_DIR"] and not observed["isolated_write"]["GIT_DIR"]
            and not observed["isolated_write"]["GIT_AUTHOR_NAME"])
    return held, observed


@probe("P4", "a read-only Claude lane runs in plan mode with project-only settings, strict MCP config, no session persistence, and denies Agent/Task/web/edit tools; no bypass token appears")
def p4(lab: Lab):
    lab.control({"claude": {"reported_model": "claude-opus-5-5"}})
    r = lab.run(lab.lane("claude", parent="codex", model="opus", effort="max"))
    argv = lab.calls()[-1]["argv"]
    def after(flag):
        return argv[argv.index(flag) + 1] if flag in argv else None
    bypass = [t for t in argv if any(b in t for b in ("bypassPermissions", "dangerously", "danger-full-access"))]
    observed = {"exit": r["exit"], "permission_mode": after("--permission-mode"), "setting_sources": after("--setting-sources"),
                "strict_mcp": "--strict-mcp-config" in argv, "no_persistence": "--no-session-persistence" in argv,
                "tools": after("--tools"), "disallowed": after("--disallowed-tools"), "effort": after("--effort"),
                "prompt_on_stdin": lab.calls()[-1]["stdin"] == "probe prompt", "bypass_tokens": bypass}
    held = (r["exit"] == 0 and observed["permission_mode"] == "plan" and observed["setting_sources"] == "project"
            and observed["strict_mcp"] and observed["no_persistence"] and observed["tools"] == "Read,Grep,Glob,Bash"
            and set(observed["disallowed"].split(",")) == {"Agent", "Task", "WebSearch", "WebFetch", "Edit", "Write", "NotebookEdit"}
            and observed["prompt_on_stdin"] and not bypass)
    return held, observed


@probe("P5", "a Claude writer in a linked worktree runs acceptEdits inside the Bash sandbox with the shared .git directory on denyWrite, failIfUnavailable on and unsandboxed commands off")
def p5(lab: Lab):
    lab.control({"claude": {"reported_model": "claude-opus-5-5"}})
    r = lab.run(lab.lane("claude", parent="codex", model="opus", mode="isolated-write", cwd=lab.worktree))
    argv = lab.calls()[-1]["argv"]
    settings = json.loads(argv[argv.index("--settings") + 1])
    shared = os.path.realpath(os.path.join(lab.repo, ".git"))
    sandbox = settings["sandbox"]
    held = (r["exit"] == 0 and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
            and sandbox == {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False,
                            "failIfUnavailable": True, "filesystem": {"denyWrite": [shared]}})
    return held, {"exit": r["exit"], "settings": lab.scrub(settings), "tools": argv[argv.index("--tools") + 1]}


@probe("P6", "a Claude writer whose cwd sits inside the shared git directory is refused before preflight or model execution")
def p6(lab: Lab):
    lab.control({"claude": {}})
    inside = os.path.join(lab.repo, ".git", "lane-inside")
    os.makedirs(inside, exist_ok=True)
    r = lab.run(lab.lane("claude", parent="codex", model="opus", mode="isolated-write", cwd=inside))
    rec = r["receipt"] or {}
    held = r["exit"] == 70 and rec.get("status") == "child-failed" and rec.get("preflight", {}).get("status") == "not-run" \
        and not lab.calls()
    return held, {"exit": r["exit"], "status": rec.get("status"), "message": lab.scrub(rec.get("error", {}).get("message")),
                  "cli_calls": len(lab.calls())}


@probe("P7", "output and receipt paths are reserved exclusively: an existing receipt is never overwritten, and reserved files are private (0600)")
def p7(lab: Lab):
    lab.control({"claude": {"reported_model": "claude-opus-5-5"}})
    lane = lab.lane("claude", parent="codex", model="opus")
    with open(lane["receipt"], "w") as f:
        f.write("previous receipt\n")
    clash = lab.run(lane)
    fresh = lab.lane("claude", parent="codex", model="opus")
    ok = lab.run(fresh)
    modes = {name: mode_of(fresh[name]) for name in ("output", "receipt")}
    modes["stdout_sidecar"] = mode_of(fresh["receipt"] + ".stdout")
    held = (clash["exit"] == 64 and open(lane["receipt"]).read() == "previous receipt\n"
            and not os.path.exists(lane["output"]) and ok["exit"] == 0 and set(modes.values()) == {"0o600"})
    return held, {"clash_exit": clash["exit"], "clash_stderr": clash["stderr"].splitlines()[0][:120],
                  "previous_bytes_kept": open(lane["receipt"]).read() == "previous receipt\n",
                  "clash_output_created": os.path.exists(lane["output"]), "fresh_modes": modes}


@probe("P8", "an unavailable model is a receipt-bearing dropout; the runner never invokes the model and never substitutes another one")
def p8(lab: Lab):
    lab.control({"grok": {"models": ["grok-4.6"], "default_model": "grok-4.7"}})
    r = lab.run(lab.lane("grok", model="grok-4.7"))
    rec = r["receipt"]
    phases = [c["phase"] for c in lab.calls()]
    held = r["exit"] == 69 and rec["status"] == "unavailable-model" and phases == ["preflight"] \
        and not os.path.exists(rec["outputPath"])
    return held, {"exit": r["exit"], "status": rec["status"], "cli_phases": phases, "model": rec["model"],
                  "reportedModel": rec["reportedModel"]}


@probe("P9", "grok's preflight gets exactly one retry, 5 s later, only after an unauthenticated verdict; the model still runs once and the receipt keeps both attempts")
def p9(lab: Lab):
    lab.control({"grok": {"preflight": "logged-out-once"}})
    r = lab.run(lab.lane("grok", model="grok-4.7"))
    rec = r["receipt"]
    phases = [c["phase"] for c in lab.calls()]
    gap = lab.calls()[1]["ts"] - lab.calls()[0]["ts"]
    held = (r["exit"] == 0 and rec["status"] == "complete" and phases == ["preflight", "preflight", "model"]
            and "attempt 1 failed" in rec["preflight"]["evidence"] and "attempt 2 passed" in rec["preflight"]["evidence"]
            and gap >= 4.9)
    return held, {"exit": r["exit"], "cli_phases": phases, "retry_gap_s": round(gap, 2),
                  "preflight_evidence_head": rec["preflight"]["evidence"][:80]}


@probe("P10", "model proof: a Claude lane asked for the opus alias that reports a Sonnet revision fails as malformed-output and leaves no answer file")
def p10(lab: Lab):
    lab.control({"claude": {"reported_model": "claude-sonnet-5-5", "text": "answer from the wrong model"}})
    r = lab.run(lab.lane("claude", parent="codex", model="opus"))
    rec = r["receipt"]
    held = r["exit"] == 65 and rec["status"] == "malformed-output" and not os.path.exists(rec["outputPath"]) \
        and "was not reported" in rec["error"]["message"]
    return held, {"exit": r["exit"], "status": rec["status"], "error": rec["error"]["message"],
                  "output_exists": os.path.exists(rec["outputPath"])}


@probe("P11", "a rolling alias is proven by a provider-reported revision of the same family (opus -> claude-opus-5-5)")
def p11(lab: Lab):
    lab.control({"claude": {"reported_model": "claude-opus-5-5", "text": "PROBE-OK"}})
    r = lab.run(lab.lane("claude", parent="codex", model="opus"))
    rec = r["receipt"]
    held = (r["exit"] == 0 and rec["status"] == "complete" and rec["model"] == "opus" and rec["reportedModel"] == "claude-opus-5-5"
            and rec["modelVerified"] is True and rec["modelEvidence"] == "provider-report"
            and open(rec["outputPath"]).read() == "PROBE-OK" and rec["costUsd"] == 0.0123)
    return held, {k: rec[k] for k in ("status", "model", "reportedModel", "modelVerified", "modelEvidence", "costUsd", "usage")}


@probe("P12", "Codex reports no served model, so its proof is the pinned argv; the lane runs ephemeral with plugins, multi_agent, hooks and memories disabled")
def p12(lab: Lab):
    lab.control({"codex": {"text": "CODEX-OK"}})
    r = lab.run(lab.lane("codex", parent="claude", model="gpt-6-luna", effort="low"))
    rec = r["receipt"]
    argv = lab.calls()[-1]["argv"]
    disabled = [argv[i + 1] for i, t in enumerate(argv) if t == "--disable"]
    held = (r["exit"] == 0 and rec["status"] == "complete" and rec["reportedModel"] is None and rec["modelVerified"] is False
            and rec["modelEvidence"] == "pinned-argv" and "--ephemeral" in argv
            and argv[argv.index("--sandbox") + 1] == "read-only" and disabled == ["plugins", "multi_agent", "hooks", "memories"]
            and 'model_reasoning_effort="low"' in argv)
    return held, {"status": rec["status"], "modelEvidence": rec["modelEvidence"], "sandbox": argv[argv.index("--sandbox") + 1],
                  "disabled": disabled, "sessionId": rec["sessionId"]}


@probe("P13", "a login that fails after the preflight is child-failed, never unauthenticated (unauthenticated comes only from the preflight)")
def p13(lab: Lab):
    lab.control({"claude": {"model_exit": 1, "model_stderr": "Error: authentication failed, please run claude login\n"}})
    r = lab.run(lab.lane("claude", parent="codex", model="opus"))
    rec = r["receipt"]
    held = r["exit"] == 70 and rec["status"] == "child-failed" and "authentication failed" in rec["error"]["evidence"]
    return held, {"exit": r["exit"], "status": rec["status"], "error": rec["error"]["message"]}


@probe("P14", "--timeout is one absolute deadline across preflight and model execution, not a fresh allowance per phase")
def p14(lab: Lab):
    lab.control({"claude": {"preflight_delay_ms": 1200, "model_delay_ms": 6000, "reported_model": "claude-opus-5-5"}})
    r = lab.run(lab.lane("claude", parent="codex", model="opus", extra=["--timeout", "2.5"]))
    rec = r["receipt"]
    held = r["exit"] == 124 and rec["status"] == "timed-out" and rec["preflight"]["status"] == "passed" \
        and rec["elapsedMs"] < 4000 and rec["signal"] == "SIGTERM"
    return held, {"exit": r["exit"], "status": rec["status"], "preflight": rec["preflight"]["status"],
                  "elapsedMs": rec["elapsedMs"], "signal": rec["signal"]}


@probe("P15", "cancelling the launcher (SIGTERM) forwards the signal to the live child, removes the empty answer, and keeps a cancelled receipt plus the partial stdout")
def p15(lab: Lab):
    marker = os.path.join(lab.root, "p15.started")
    lab.control({"claude": {"started_marker": marker, "partial_stdout": "partial-bytes", "model_delay_ms": 30000}})
    lane = lab.lane("claude", parent="codex", model="opus")
    r = lab.run(lane, signal_after_marker=marker)
    rec = r["receipt"]
    sidecar = open(lane["receipt"] + ".stdout").read()
    held = (r["exit"] == 130 and rec["status"] == "cancelled" and rec["signal"] == "SIGTERM"
            and not os.path.exists(lane["output"]) and sidecar == "partial-bytes")
    return held, {"exit": r["exit"], "status": rec["status"], "signal": rec["signal"], "error": rec["error"]["message"],
                  "answer_exists": os.path.exists(lane["output"]), "stdout_sidecar": sidecar, "elapsed_s": r["elapsed_s"]}


@probe("P16", "a versioned Claude pin of a rolling family must be normalized by the parent first; the runner refuses it")
def p16(lab: Lab):
    lab.control({})
    r = lab.run(lab.lane("claude", parent="codex", model="claude-opus-5-6"))
    held = r["exit"] == 64 and "normalize it to opus" in r["stderr"] and not lab.calls()
    return held, {"exit": r["exit"], "stderr_head": r["stderr"].splitlines()[0]}


@probe("P17", "a read-only-only provider (devin) refuses an isolated-write lane before launch")
def p17(lab: Lab):
    lab.control({})
    r = lab.run(lab.lane("devin", parent="claude", model="swe-2", effort="max", mode="isolated-write", cwd=lab.worktree))
    held = r["exit"] == 64 and "does not support isolated-write" in r["stderr"] and not lab.calls()
    return held, {"exit": r["exit"], "stderr_head": r["stderr"].splitlines()[0]}


@probe("P18", "there is no implicit timeout: a 12 s model call without --timeout completes")
def p18(lab: Lab):
    lab.control({"claude": {"model_delay_ms": 12000, "reported_model": "claude-opus-5-5"}})
    r = lab.run(lab.lane("claude", parent="codex", model="opus"))
    rec = r["receipt"]
    held = r["exit"] == 0 and rec["status"] == "complete" and rec["elapsedMs"] >= 12000
    return held, {"exit": r["exit"], "status": rec["status"], "elapsedMs": rec["elapsedMs"]}


@probe("P19", "an exit-0 child whose stdout is not the provider's JSON is malformed-output, and the answer file is removed")
def p19(lab: Lab):
    lab.control({"claude": {"model_stdout_raw": "I am not JSON"}})
    r = lab.run(lab.lane("claude", parent="codex", model="opus"))
    rec = r["receipt"]
    held = r["exit"] == 65 and rec["status"] == "malformed-output" and not os.path.exists(rec["outputPath"])
    return held, {"exit": r["exit"], "status": rec["status"], "error": rec["error"]["message"]}


@probe("P20", "for an inherited-environment lane only the parent's fixed identity list is removed; other CLAUDE_CODE_* variables a current Claude Code exports still reach a Codex lane")
def p20(lab: Lab):
    lab.control({"codex": {"text": "CODEX-OK"}})
    parent_env = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_CHILD_SESSION": "1",
                  "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CODE_MESSAGING_TOKEN": "probe-token",
                  "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/probe.sock", "CLAUDE_PID": "123", "CLAUDE_EFFORT": "high"}
    r = lab.run(lab.lane("codex", parent="claude", model="gpt-6-luna"), parent_env)
    env = lab.calls()[-1]["env"]
    removed = sorted(k for k in parent_env if k not in env)
    passed = sorted(k for k in parent_env if k in env)
    held = r["exit"] == 0 and removed == ["CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID"] \
        and "CLAUDE_CODE_MESSAGING_TOKEN" in passed
    return held, {"exit": r["exit"], "removed": removed, "passed_to_codex": passed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clone")
    parser.add_argument("--out", default=os.path.join(HERE, "probe_output.json"))
    args = parser.parse_args()
    lab = Lab(args.clone)
    head = subprocess.run(["git", "-C", lab.clone, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    results = []
    try:
        for pid, claim, fn in PROBES:
            try:
                held, observed = fn(lab)
            except Exception as error:
                held, observed = False, {"exception": repr(error)}
            results.append({"probe": pid, "claim": claim, "held": bool(held), "observed": lab.scrub(observed)})
            print(f"{pid} {'held' if held else 'DID NOT HOLD'}: {claim}")
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)
    report = {"target": "ericlitman/open-pstack", "commit": head,
              "bun": subprocess.run(["bun", "--version"], capture_output=True, text=True).stdout.strip(),
              "held": sum(r["held"] for r in results), "total": len(results), "probes": results}
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    print(f"{report['held']}/{report['total']} held -> {args.out}")
    return 0 if report["held"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
