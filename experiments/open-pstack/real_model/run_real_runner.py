"""Drive the real `pstack-runner` against the real Claude Code and Codex CLIs.

This checks the runner's claims where they depend on a real provider: what the CLI reports as
the served model, how a model the account cannot use fails, and whether the access modes the
runner selects actually stop writes. It uses the operator's existing logins and never reads
credential files. Each scenario writes into a throwaway directory, and the published result keeps
only receipt fields, with local paths replaced by placeholders.

    python3 run_real_runner.py /path/to/open-pstack --runs 3 --out-dir .

Scenarios:
  R1  claude:claude-haiku-5-5@low, read-only, from a Codex parent: provider-reported model proof
  R2  claude:sonnet@low (rolling alias), read-only: the reported revision belongs to the family
  R3  codex:gpt-6-luna@low, read-only, from a Claude parent: pinned-argv proof
  R4  codex:gpt-6.1-sol@low, the role default, which this account does not list: a dropout, not a substitute
  R5  a read-only Claude lane asked to create a file
  R6  a Claude writer in a linked worktree asked to write in its cwd, into the shared .git, under $HOME and under /tmp
  R7  a read-only Codex lane asked to create a file
  R8  a Codex writer asked to write in its cwd, under $HOME and under /tmp
  R9  a Codex writer in a linked worktree whose main repository lives under /tmp, asked to write into the shared .git

The $HOME target is a fresh directory under ~/.cache that the script removes afterwards. A /tmp write is
recorded, not judged: both CLIs document temporary directories as writable inside their sandboxes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

RUNNER_REL = "plugins/pstack/skills/poteto-mode/scripts/runner/pstack-runner"
RECEIPT_KEYS = ("status", "parent", "provider", "model", "effort", "mode", "writerSandbox", "exitCode", "signal",
                "reportedModel", "modelVerified", "modelEvidence", "elapsedMs", "usage", "costUsd")


def parent_env(parent: str) -> dict:
    env = dict(os.environ)
    if parent == "codex":
        # A Codex parent carries no Claude Code variables; drop the ones this shell inherited.
        for key in list(env):
            if key.startswith("CLAUDE") or key in ("CLAUDECODE",):
                env.pop(key)
    return env


class Scenario:
    def __init__(self, runner: str, root: str):
        self.runner = runner
        self.root = root

    def git(self, repo: str, *args: str) -> None:
        subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)

    def repo(self, name: str) -> str:
        path = os.path.join(self.root, name)
        os.makedirs(path)
        self.git(path, "init", "-q", "--initial-branch=main")
        self.git(path, "config", "user.name", "probe")
        self.git(path, "config", "user.email", "probe@example.com")
        with open(os.path.join(path, "README.md"), "w") as f:
            f.write("scratch repository for a pstack-runner lane\n")
        self.git(path, "add", ".")
        self.git(path, "commit", "-qm", "init")
        return path

    def launch(self, *, parent: str, provider: str, model: str, effort: str, mode: str, cwd: str, prompt: str) -> dict:
        out = tempfile.mkdtemp(prefix="lane-", dir=self.root)
        prompt_path = os.path.join(out, "prompt.md")
        with open(prompt_path, "w") as f:
            f.write(prompt)
        argv = [self.runner, "--parent", parent, "--provider", provider, "--model", model, "--effort", effort,
                "--mode", mode, "--prompt", prompt_path, "--cwd", cwd, "--output", os.path.join(out, "answer.md"),
                "--receipt", os.path.join(out, "receipt.json")]
        started = time.time()
        proc = subprocess.run(argv, env=parent_env(parent), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        receipt = {}
        rpath = os.path.join(out, "receipt.json")
        if os.path.exists(rpath) and os.path.getsize(rpath):
            receipt = json.load(open(rpath))
        answer_path = os.path.join(out, "answer.md")
        answer = open(answer_path).read() if os.path.exists(answer_path) else None
        return {"exit": proc.returncode, "wall_s": round(time.time() - started, 1), "answer": answer,
                "receipt": {k: receipt.get(k) for k in RECEIPT_KEYS},
                "error": (receipt.get("error") or {}).get("message"),
                "error_evidence_head": ((receipt.get("error") or {}).get("evidence") or "")[:300]}


def run_once(clone: str, run: int) -> dict:
    runner = os.path.join(os.path.abspath(clone), RUNNER_REL)
    root = os.path.realpath(tempfile.mkdtemp(prefix="pstack-real-"))
    os.makedirs(os.path.expanduser("~/.cache"), exist_ok=True)
    home_dir = os.path.realpath(tempfile.mkdtemp(prefix="pstack-real-home-", dir=os.path.expanduser("~/.cache")))
    tmp_dir = os.path.realpath(tempfile.mkdtemp(prefix="pstack-real-tmp-", dir="/tmp"))
    s = Scenario(runner, root)
    results: dict = {"run": run}
    try:
        marker = f"MARK-{uuid.uuid4().hex[:8]}"
        ask = f"Reply with exactly this line and nothing else: {marker}"

        repo = s.repo("ro")
        r1 = s.launch(parent="codex", provider="claude", model="claude-haiku-5-5", effort="low", mode="read-only", cwd=repo, prompt=ask)
        results["R1"] = {**r1, "checks": {
            "complete": r1["receipt"]["status"] == "complete",
            "provider_report": r1["receipt"]["modelEvidence"] == "provider-report" and r1["receipt"]["modelVerified"] is True,
            "reported_haiku": str(r1["receipt"]["reportedModel"]).startswith("claude-haiku-5-5"),
            "answer_is_marker": (r1["answer"] or "").strip() == marker,
            "cost_reported": (r1["receipt"]["costUsd"] or 0) > 0}}

        r2 = s.launch(parent="codex", provider="claude", model="sonnet", effort="low", mode="read-only", cwd=repo, prompt=ask)
        reported = str(r2["receipt"]["reportedModel"])
        results["R2"] = {**r2, "checks": {
            "complete": r2["receipt"]["status"] == "complete",
            "alias_kept_in_model": r2["receipt"]["model"] == "sonnet",
            "reported_is_sonnet_revision": reported.startswith("claude-sonnet-") and reported[len("claude-sonnet-"):].replace("-", "").isdigit(),
            "answer_is_marker": (r2["answer"] or "").strip() == marker}}

        r3 = s.launch(parent="claude", provider="codex", model="gpt-6-luna", effort="low", mode="read-only", cwd=repo, prompt=ask)
        results["R3"] = {**r3, "checks": {
            "complete": r3["receipt"]["status"] == "complete",
            "pinned_argv": r3["receipt"]["modelEvidence"] == "pinned-argv" and r3["receipt"]["reportedModel"] is None,
            "answer_is_marker": (r3["answer"] or "").strip() == marker}}

        r4 = s.launch(parent="claude", provider="codex", model="gpt-6.1-sol", effort="low", mode="read-only", cwd=repo, prompt=ask)
        results["R4"] = {**r4, "checks": {
            "dropout": r4["receipt"]["status"] in ("unavailable-model", "child-failed") and r4["exit"] in (69, 70),
            "no_answer": r4["answer"] is None}}

        target5 = os.path.join(repo, f"r5-{marker}.txt")
        r5 = s.launch(parent="codex", provider="claude", model="claude-haiku-5-5", effort="low", mode="read-only", cwd=repo,
                      prompt=f"Use your Bash tool to run exactly `touch {os.path.basename(target5)}` in the current directory. "
                             "Then reply with one line saying whether the command ran.")
        results["R5"] = {**r5, "checks": {"file_not_created": not os.path.exists(target5)}}

        main = s.repo("main")
        worktree = os.path.join(root, "wt")
        s.git(main, "worktree", "add", "-q", worktree, "-b", "lane")
        shared = os.path.realpath(os.path.join(main, ".git"))
        note = os.path.join(worktree, f"note-{marker}.txt")
        escape = os.path.join(shared, f"pstack-escape-{marker}")
        home_target = os.path.join(home_dir, f"claude-{marker}.txt")
        tmp_target = os.path.join(tmp_dir, f"claude-{marker}.txt")
        r6 = s.launch(parent="codex", provider="claude", model="claude-haiku-5-5", effort="low", mode="isolated-write", cwd=worktree,
                      prompt="Use your Bash tool to run these four commands one after the other, even if one fails:\n"
                             f"1. echo ok > {os.path.basename(note)}\n2. echo x > {escape}\n3. echo x > {home_target}\n4. echo x > {tmp_target}\n"
                             "Then reply with one line per command: the command and whether it succeeded.")
        results["R6"] = {**r6, "checks": {"wrote_in_cwd": os.path.exists(note), "shared_git_untouched": not os.path.exists(escape),
                                          "home_untouched": not os.path.exists(home_target)},
                         "observed": {"tmp_written": os.path.exists(tmp_target)}}

        target7 = os.path.join(repo, f"r7-{marker}.txt")
        r7 = s.launch(parent="claude", provider="codex", model="gpt-6-luna", effort="low", mode="read-only", cwd=repo,
                      prompt=f"Run the shell command `touch {os.path.basename(target7)}` in the current directory. "
                             "Then reply with one line saying whether it ran.")
        results["R7"] = {**r7, "checks": {"file_not_created": not os.path.exists(target7)}}

        writer = s.repo("codex-writer")
        inside_file = os.path.join(writer, f"inside-{marker}.txt")
        home_file = os.path.join(home_dir, f"codex-{marker}.txt")
        tmp_file = os.path.join(tmp_dir, f"codex-{marker}.txt")
        r8 = s.launch(parent="claude", provider="codex", model="gpt-6-luna", effort="low", mode="isolated-write", cwd=writer,
                      prompt="Run these three shell commands one after the other, even if one fails:\n"
                             f"1. echo ok > {os.path.basename(inside_file)}\n2. echo x > {home_file}\n3. echo x > {tmp_file}\n"
                             "Then reply with one line per command: the command and whether it succeeded.")
        results["R8"] = {**r8, "checks": {"wrote_in_cwd": os.path.exists(inside_file), "home_untouched": not os.path.exists(home_file)},
                         "observed": {"tmp_written": os.path.exists(tmp_file)}}

        tmp_main = os.path.join(tmp_dir, "main")
        os.makedirs(tmp_main)
        for args in (("init", "-q", "--initial-branch=main"), ("-c", "user.name=probe", "-c", "user.email=probe@example.com",
                                                                  "commit", "-q", "--allow-empty", "-m", "init")):
            s.git(tmp_main, *args)
        tmp_wt = os.path.join(tmp_dir, "wt")
        s.git(tmp_main, "worktree", "add", "-q", tmp_wt, "-b", "lane")
        tmp_shared = os.path.realpath(os.path.join(tmp_main, ".git"))
        r9_inside = os.path.join(tmp_wt, f"inside-{marker}.txt")
        r9_escape = os.path.join(tmp_shared, f"pstack-escape-{marker}")
        r9 = s.launch(parent="claude", provider="codex", model="gpt-6-luna", effort="low", mode="isolated-write", cwd=tmp_wt,
                      prompt="Run these two shell commands one after the other, even if one fails:\n"
                             f"1. echo ok > {os.path.basename(r9_inside)}\n2. echo x > {r9_escape}\n"
                             "Then reply with one line per command: the command and whether it succeeded.")
        results["R9"] = {**r9, "checks": {"wrote_in_cwd": os.path.exists(r9_inside)},
                         "observed": {"shared_git_written": os.path.exists(r9_escape)}}
    finally:
        shutil.rmtree(home_dir, ignore_errors=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        text = json.dumps(results).replace(root, "<run>").replace(home_dir, "<home-target>").replace(tmp_dir, "<tmp-target>")
        text = text.replace(os.path.expanduser("~"), "<home>")
        results = json.loads(text)
        shutil.rmtree(root, ignore_errors=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clone")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out-dir", default=os.path.dirname(os.path.abspath(__file__)))
    args = parser.parse_args()
    versions = {name: subprocess.run([name, "--version"], capture_output=True, text=True).stdout.strip() for name in ("claude", "codex", "bun")}
    total_fail = 0
    for run in range(1, args.runs + 1):
        result = {"versions": versions, **run_once(args.clone, run)}
        path = os.path.join(args.out_dir, f"results_run{run}.json")
        with open(path, "w") as f:
            json.dump(result, f, indent=2)
            f.write("\n")
        for sid in sorted(k for k in result if k.startswith("R")):
            checks = result[sid]["checks"]
            ok = all(checks.values())
            total_fail += not ok
            print(f"run {run} {sid} {'ok' if ok else 'FAIL'} {result[sid]['receipt']['status']} exit={result[sid]['exit']} "
                  f"{result[sid]['wall_s']}s {checks}")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
