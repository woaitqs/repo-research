"""Drive the reproduction (minipstack) against the real Claude Code and Codex CLIs.

Two `interrogate` panels review the same diff, which has a planted defect: `percentile(values, 100)`
indexes one past the end. Panel 1 runs from a Claude parent, panel 2 from a Codex parent, so each
provider appears once as a native lane and once through minipstack's external runner.

  panel 1, parent claude: claude:claude-haiku-5-5@low (native), codex:gpt-6-luna@low (runner),
                          codex:gpt-6.1-sol@low (runner; this account's Codex does not serve it),
                          grok:grok-4.7@xhigh (runner; no grok CLI on this host)
  panel 2, parent codex:  claude:claude-haiku-5-5@low (runner), claude:sonnet@low (runner),
                          codex:gpt-6-luna@low (native)

A native lane here calls the parent's own CLI directly, standing in for the parent's subagent
primitive. The checks are about the architecture: which lanes count, how each was proven, that
dropouts stay dropouts, and what the agreement map says.

    PYTHONPATH=../src python3 run_mini_real.py --runs 3 --out mini_results.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from minipstack.dispatch import interrogate  # noqa: E402

DIFF = """--- a/stats.py
+++ b/stats.py
@@ -0,0 +1,7 @@
 1 +def percentile(values, p):
 2 +    ordered = sorted(values)
 3 +    k = (len(ordered) - 1) * p / 100
 4 +    lower = int(k)
 5 +    upper = lower + 1
 6 +    weight = k - lower
 7 +    return ordered[lower] * (1 - weight) + ordered[upper] * weight
"""
INTENT = "Add percentile(values, p) with linear interpolation for p in 0..100."
PLANTED = {5, 7}  # p == 100 makes upper == len(ordered); the IndexError surfaces on line 7


def clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}


def native_claude(a) -> str | None:
    run = subprocess.run(["claude", "-p", "--model", a.model, "--effort", a.effort, "--permission-mode", "plan",
                          "--setting-sources", "project", "--strict-mcp-config", "--tools", "Read,Grep,Glob",
                          "--no-session-persistence", "--output-format", "json"],
                         input=a.prompt, capture_output=True, text=True, env=clean_env())
    return json.loads(run.stdout).get("result") if run.returncode == 0 else None


def native_codex(a) -> str | None:
    run = subprocess.run(["codex", "exec", "--model", a.model, "--config", f'model_reasoning_effort="{a.effort}"', "--sandbox", "read-only",
                          "--skip-git-repo-check", "--ephemeral", "--json", "-"], input=a.prompt, capture_output=True, text=True,
                         env=clean_env())
    text = None
    for line in run.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
            text = event["item"].get("text")
    return text if run.returncode == 0 else None


def panel(parent: str, reviewers: list[str], root: str, cwd: str) -> dict:
    native = native_claude if parent == "claude" else native_codex
    verdict = interrogate(parent, reviewers, INTENT, DIFF, os.path.join(root, f"panel-{parent}"), native=native, cwd=cwd,
                          source_env=clean_env())
    found = {f["line"] for f in verdict["agreement"] + verdict["single"]}
    verdict["planted_found_by"] = sorted({d for f in verdict["agreement"] + verdict["single"] if f["line"] in PLANTED for d in f["raised_by"]})
    verdict["planted_in_agreement"] = any(f["line"] in PLANTED for f in verdict["agreement"])
    verdict["lines_cited"] = sorted(found)
    receipts = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(root, f"panel-{parent}")) for f in fs if f == "receipt.json")
    verdict["receipts"] = [{k: json.load(open(p)).get(k) for k in ("provider", "model", "status", "reportedModel", "modelEvidence", "costUsd", "elapsedMs")}
                           for p in receipts]
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "mini_results.json"))
    args = parser.parse_args()
    report = {"versions": {n: subprocess.run([n, "--version"], capture_output=True, text=True).stdout.strip() for n in ("claude", "codex")},
              "intent": INTENT, "diff": DIFF, "planted_lines": sorted(PLANTED), "runs": []}
    for run in range(1, args.runs + 1):
        root = os.path.realpath(tempfile.mkdtemp(prefix="minipstack-real-"))
        cwd = os.path.join(root, "repo")
        os.makedirs(cwd)
        subprocess.run(["git", "init", "-q", cwd], check=True)
        try:
            p1 = panel("claude", ["claude:claude-haiku-5-5@low", "codex:gpt-6-luna@low", "codex:gpt-6.1-sol@low", "grok:grok-4.7@xhigh"], root, cwd)
            p2 = panel("codex", ["claude:claude-haiku-5-5@low", "claude:sonnet@low", "codex:gpt-6-luna@low"], root, cwd)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        checks = {
            "p1_counts": [r["counted"] for r in p1["reviewers"]] == [True, True, False, False],
            "p1_dropouts_named": [r["status"] for r in p1["reviewers"][2:]] == ["unavailable-model", "unavailable-cli"],
            "p1_codex_pinned_argv": any(r["provider"] == "codex" and r["status"] == "complete" and r["modelEvidence"] == "pinned-argv" for r in p1["receipts"]),
            "p2_all_counted": all(r["counted"] for r in p2["reviewers"]),
            "p2_claude_provider_report": sum(r["provider"] == "claude" and r["modelEvidence"] == "provider-report" for r in p2["receipts"]) == 2,
        }
        entry = {"run": run, "checks": checks, "panel_claude_parent": p1, "panel_codex_parent": p2}
        report["runs"].append(entry)
        print(f"run {run}: {checks} planted-agreement p1={p1['planted_in_agreement']} p2={p2['planted_in_agreement']} "
              f"lines p1={p1['lines_cited']} p2={p2['lines_cited']}")
        with open(args.out, "w") as f:
            text = re.sub(r"/[^\"\s]*minipstack-real-[A-Za-z0-9_]+", "<run>", json.dumps(report, indent=2))
            f.write(text.replace(os.path.expanduser("~"), "<home>") + "\n")
    return 0 if all(all(r["checks"].values()) for r in report["runs"]) else 1


if __name__ == "__main__":
    sys.exit(main())
