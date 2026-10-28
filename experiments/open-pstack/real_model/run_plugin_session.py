"""Load the pinned plugin into real headless Claude Code sessions and watch what the model does.

pstack's entry point is a context injection: a SessionStart hook prints an 11-line mandate that
tells the model to invoke `pstack:poteto-mode` for non-trivial engineering work. Whether that
routing happens is a model-side behaviour, so it is measured here, not assumed.

Each session runs `claude -p` with `--plugin-dir <clone>/plugins/pstack --setting-sources project`
(so only this plugin and Claude Code's built-ins load), in a fresh fixture repository, with
`--output-format stream-json --verbose --include-hook-events`. Edits, Bash and sub-agents are
disallowed so a session can only read and route; nothing in the fixture changes.

    python3 run_plugin_session.py /path/to/open-pstack --models haiku sonnet --runs 3 --out plugin_sessions.json

Prompts:
  bug      a reproducible defect plus "add a test": the mandate allows `pstack:tdd` directly, or poteto-mode
  feature  a small feature with edge cases and tests: non-trivial work, the mandate's poteto-mode case
  trivial  a one-sentence factual question about git: the mandate says not to route it

A session counts as routed when it invokes any pstack skill through the Skill tool.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

PROMPTS = {
    "bug": "In this repository, stats.mean([]) raises ZeroDivisionError. Fix it so the mean of an empty list is 0.0, "
           "and add a regression test.",
    "feature": "Add a percentile(values, p) function to stats.py that uses linear interpolation, rejects p outside "
               "0..100 and empty input with ValueError, and add tests for it.",
    "trivial": "What does `git stash` do? Answer in one sentence.",
}

FIXTURE = {
    "stats.py": "def mean(values):\n    return sum(values) / len(values)\n",
    "test_stats.py": "from stats import mean\n\n\ndef test_mean():\n    assert mean([1, 2, 3]) == 2\n",
    "README.md": "A tiny statistics module.\n",
}


def fixture(root: str) -> str:
    repo = tempfile.mkdtemp(prefix="fixture-", dir=root)
    for name, body in FIXTURE.items():
        with open(os.path.join(repo, name), "w") as f:
            f.write(body)
    for args in (("init", "-q", "--initial-branch=main"), ("add", "."),
                 ("-c", "user.name=probe", "-c", "user.email=probe@example.com", "commit", "-qm", "init")):
        subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)
    return repo


def session(plugin: str, model: str, prompt: str, cwd: str) -> list[dict]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    argv = ["claude", "-p", prompt, "--plugin-dir", plugin, "--setting-sources", "project", "--model", model,
            "--max-turns", "10", "--output-format", "stream-json", "--verbose", "--include-hook-events",
            "--no-session-persistence", "--disallowedTools", "Bash,Edit,Write,NotebookEdit,Agent,Task,WebFetch,WebSearch"]
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def summarize(events: list[dict], plugin: str) -> dict:
    hook = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "hook_response"), None)
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    tool_uses = []
    for e in events:
        if e.get("type") != "assistant":
            continue
        for block in e.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                tool_uses.append({"name": block.get("name"), "input": block.get("input", {})})
    skills = [t["input"].get("skill") for t in tool_uses if t["name"] == "Skill"]
    reads = [t["input"].get("file_path", "") for t in tool_uses if t["name"] == "Read"]
    def rel(path: str) -> str:
        return path.replace(plugin, "<plugin>") if path.startswith(plugin) else "<fixture>/" + os.path.basename(path)
    todos = [t["input"].get("todos", []) for t in tool_uses if t["name"] == "TodoWrite"]
    first_todos = [item.get("content", "") for item in (todos[0] if todos else [])]
    result = next((e for e in events if e.get("type") == "result"), {})
    return {
        "hook_fired": hook is not None and "You have pstack." in (hook.get("output") or ""),
        "pstack_skills_listed": len([s for s in init.get("skills", []) if s.startswith("pstack:")]),
        "principle_skills_listed": len([s for s in init.get("skills", []) if s.startswith("pstack:principle-")]),
        "first_tool": tool_uses[0]["name"] if tool_uses else None,
        "skills_invoked": skills,
        "routed": any(str(skill).startswith("pstack:") for skill in skills),
        "routed_to_poteto_mode": "pstack:poteto-mode" in skills,
        "plugin_files_read": [rel(p) for p in reads if p.startswith(plugin)],
        "fixture_files_read": [rel(p) for p in reads if not p.startswith(plugin)],
        "playbook_read": any("/playbooks/" in p for p in reads),
        "principle_leaves_read": sorted({p.split("/skills/")[1].split("/")[0] for p in reads if "/skills/principle-" in p}),
        "first_todolist": first_todos,
        "tool_sequence": [t["name"] for t in tool_uses],
        "result_subtype": result.get("subtype"),
        "num_turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"),
        "models": list((result.get("modelUsage") or {}).keys()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clone")
    parser.add_argument("--models", nargs="+", default=["haiku", "sonnet"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin_sessions.json"))
    args = parser.parse_args()
    plugin = os.path.realpath(os.path.join(args.clone, "plugins/pstack"))
    root = os.path.realpath(tempfile.mkdtemp(prefix="pstack-sessions-"))
    report = {"claude": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
              "prompts": PROMPTS, "sessions": []}
    try:
        for model in args.models:
            for name, prompt in PROMPTS.items():
                for run in range(1, args.runs + 1):
                    repo = fixture(root)
                    summary = summarize(session(plugin, model, prompt, repo), plugin)
                    unchanged = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True).stdout == ""
                    entry = {"model": model, "prompt": name, "run": run, "fixture_unchanged": unchanged, **summary}
                    report["sessions"].append(entry)
                    print(f"{model} {name} run {run}: hook={entry['hook_fired']} routed={entry['routed']} "
                          f"skills={entry['skills_invoked']} playbook={entry['playbook_read']} "
                          f"principles={len(entry['principle_leaves_read'])} tools={entry['tool_sequence'][:8]} ${entry['cost_usd']}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    text = json.dumps(report, indent=2).replace(os.path.expanduser("~"), "<home>")
    with open(args.out, "w") as f:
        f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
