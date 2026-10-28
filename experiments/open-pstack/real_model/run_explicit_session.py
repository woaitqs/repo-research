"""Invoke `/pstack:poteto-mode` explicitly in a real headless Claude Code session and record what follows.

`run_plugin_session.py` measures routing: whether the model enters pstack on its own. This script
measures follow-through once it has: does the parent read the matched playbook, open a todolist
from its steps, read the dispatch reference and principle leaves, and launch lanes through the
documented primitives (a `pstack-*` agent, or `pstack-runner` through Bash)? On this host only the
Claude and Codex CLIs are installed and the account's Codex does not serve the role-default Sol,
so the default sheet's Grok and Sol lanes must end as dropouts. The script records how the parent
handles that.

The fixture is a throwaway git repository. Edits are accepted, and Bash runs inside Claude Code's
sandbox with auto-allow, the same writer setup pstack gives its own Claude lanes. Each session is
capped with `--max-turns` and `--max-budget-usd`.

    python3 run_explicit_session.py /path/to/open-pstack --models sonnet opus --runs 3 --out explicit_sessions.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_plugin_session import FIXTURE, fixture  # noqa: E402

TASK = ("/pstack:poteto-mode Add a percentile(values, p) function to stats.py that uses linear interpolation, rejects p "
        "outside 0..100 and empty input with ValueError, and add tests. Do not open a pull request.")
SETTINGS = json.dumps({"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}})


def run(plugin: str, model: str, cwd: str, budget: float, turns: int) -> list[dict]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    argv = ["claude", "-p", TASK, "--plugin-dir", plugin, "--setting-sources", "project", "--model", model,
            "--max-turns", str(turns), "--max-budget-usd", str(budget), "--permission-mode", "acceptEdits",
            "--settings", SETTINGS, "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=3600)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def summarize(events: list[dict], plugin: str, cwd: str) -> dict:
    uses = []
    for e in events:
        if e.get("type") == "assistant" and not e.get("parent_tool_use_id"):
            uses += [b for b in e.get("message", {}).get("content", []) if b.get("type") == "tool_use"]
    reads = [u["input"].get("file_path", "") for u in uses if u["name"] == "Read"]
    plugin_reads = [p.replace(plugin, "<plugin>") for p in reads if p.startswith(plugin)]
    todo_calls = [u["input"] for u in uses if u["name"] in ("TodoWrite", "TaskCreate", "TaskUpdate")]
    first_todo = []
    if todo_calls:
        first = todo_calls[0]
        items = first.get("todos") or first.get("tasks") or ([first] if "subject" in first or "content" in first else [])
        first_todo = [i.get("content") or i.get("subject") or i.get("title") or "" for i in items if isinstance(i, dict)]
    agents = [{"subagent_type": u["input"].get("subagent_type"), "background": u["input"].get("run_in_background"),
               "model": u["input"].get("model")} for u in uses if u["name"] in ("Agent", "Task")]
    bash = [u["input"].get("command", "") for u in uses if u["name"] == "Bash"]
    runner_calls = [c.replace(plugin, "<plugin>").replace(cwd, "<fixture>") for c in bash if "pstack-runner" in c]
    result = next((e for e in events if e.get("type") == "result"), {})
    first = next((e for e in events if e.get("type") == "assistant" and not e.get("parent_tool_use_id")), {})
    usage = first.get("message", {}).get("usage", {})
    first_prompt_tokens = sum(usage.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    test_run = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=cwd, capture_output=True, text=True)
    return {
        # The stream does not echo the expanded skill body; the first call's prompt size shows it arrived
        # (about 24.9k tokens for a plain prompt in this setup, about 32.1k with /pstack:poteto-mode).
        "first_call_prompt_tokens": first_prompt_tokens,
        "plugin_files_read": plugin_reads,
        "playbook_read": [p for p in plugin_reads if "/playbooks/" in p],
        "dispatch_reference_read": any(p.endswith("provider-dispatch.md") for p in plugin_reads),
        "catalog_read": any(p.endswith("model-catalog.md") for p in plugin_reads),
        "principle_leaves_read": sorted({p.split("/skills/")[1].split("/")[0] for p in plugin_reads if "/skills/principle-" in p}),
        "skills_invoked": [u["input"].get("skill") for u in uses if u["name"] == "Skill"],
        "first_todolist": first_todo,
        "agent_calls": agents,
        "runner_calls": runner_calls,
        "tool_counts": {name: sum(1 for u in uses if u["name"] == name) for name in sorted({u["name"] for u in uses})},
        "files_changed": subprocess.run(["git", "-C", cwd, "status", "--porcelain"], capture_output=True, text=True).stdout.split("\n")[:-1],
        "fixture_tests": (test_run.stdout.strip().splitlines() or ["(no output)"])[-1],
        "result_subtype": result.get("subtype"),
        "num_turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"),
        "models": list((result.get("modelUsage") or {}).keys()),
        "final_text": (result.get("result") or "")[:1500],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clone")
    parser.add_argument("--models", nargs="+", default=["sonnet", "opus"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--budget", type=float, default=1.5)
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "explicit_sessions.json"))
    args = parser.parse_args()
    plugin = os.path.realpath(os.path.join(args.clone, "plugins/pstack"))
    root = os.path.realpath(tempfile.mkdtemp(prefix="pstack-explicit-"))
    report = {"claude": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(), "task": TASK,
              "fixture": FIXTURE, "budget_usd": args.budget, "max_turns": args.turns, "sessions": []}
    try:
        for model in args.models:
            for n in range(1, args.runs + 1):
                repo = fixture(root)
                entry = {"model": model, "run": n, **summarize(run(plugin, model, repo, args.budget, args.turns), plugin, repo)}
                report["sessions"].append(entry)
                print(f"{model} run {n}: playbook={entry['playbook_read']} dispatch={entry['dispatch_reference_read']} "
                      f"principles={entry['principle_leaves_read']} todo={len(entry['first_todolist'])} agents={entry['agent_calls']} "
                      f"runner={len(entry['runner_calls'])} tests={entry['fixture_tests']} {entry['result_subtype']} ${entry['cost_usd']}")
                with open(args.out, "w") as f:
                    f.write(json.dumps(report, indent=2).replace(os.path.expanduser("~"), "<home>").replace(root, "<run>") + "\n")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
