"""A narrated walk through the reproduction. Each scene states a claim and checks it.

    python3 -m minipstack.demo      (or ./run.sh demo)

The provider CLIs are small scripted fakes written into a temporary bin directory, so the demo
needs no network and no login.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import orch, render, sheet, skills
from .dispatch import fan_out
from .registry import UsageError, resolve_lane
from .runner import run_lane

FAKE = r'''
import json, os, sys, time
bin_dir, name, args = sys.argv[1], sys.argv[2], sys.argv[3:]
ctl = json.load(open(os.path.join(bin_dir, "control.json"))).get(name, {})
with open(os.path.join(bin_dir, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"name": name, "argv": args, "env": sorted(os.environ)}) + "\n")
if args[:2] in (["auth", "status"], ["login", "status"]) or args[:1] == ["models"]:
    time.sleep(ctl.get("preflight_delay", 0))
    if name == "claude": print(json.dumps({"loggedIn": True}))
    elif name == "codex": print("Logged in using ChatGPT")
    else: print("You are logged in\nAvailable models:\n" + "\n".join("  " + m for m in ctl.get("models", ["grok-4.7"])))
    sys.exit(0)
sys.stdin.read() if not sys.stdin.isatty() else None
time.sleep(ctl.get("delay", 0))
if ctl.get("exit"):
    sys.stderr.write(ctl.get("stderr", "")); sys.exit(ctl["exit"])
text = ctl.get("text", "ANSWER")
if name == "claude":
    print(json.dumps({"type": "result", "result": text, "total_cost_usd": 0.01, "modelUsage": {ctl.get("reported", "claude-opus-5-5"): {}}}))
elif name == "codex":
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
else:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text, "modelUsage": {"grok-4.7-build": {}}}))
'''


class Stage:
    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="minipstack-demo-"))
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin)
        fake = os.path.join(self.root, "fake.py")
        open(fake, "w").write(FAKE)
        for name in ("claude", "codex", "grok"):
            path = os.path.join(self.bin, name)
            open(path, "w").write(f"#!{sys.executable} -I\nimport runpy, sys\nsys.argv = [{fake!r}, {self.bin!r}, {name!r}, *sys.argv[1:]]\n"
                                  f"runpy.run_path({fake!r}, run_name='__main__')\n")
            os.chmod(path, 0o755)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        self.env = {"PATH": os.pathsep.join([self.bin, "/usr/bin", "/bin"]), "HOME": self.root, "CLAUDECODE": "1",
                    "CLAUDE_CODE_SESSION_ID": "parent"}
        self.n = 0

    def control(self, **value):
        open(os.path.join(self.bin, "control.json"), "w").write(json.dumps(value))
        if os.path.exists(os.path.join(self.bin, "calls.jsonl")):
            os.unlink(os.path.join(self.bin, "calls.jsonl"))

    def calls(self):
        path = os.path.join(self.bin, "calls.jsonl")
        return [json.loads(line) for line in open(path)] if os.path.exists(path) else []

    def lane(self, provider, model, effort="max", parent="claude", timeout_s=None):
        self.n += 1
        out = os.path.join(self.root, "out", str(self.n))
        os.makedirs(out)
        open(os.path.join(out, "prompt.md"), "w").write("review this")
        return resolve_lane(parent=parent, provider=provider, model=model, effort=effort, mode="read-only",
                            prompt_path=os.path.join(out, "prompt.md"), cwd=self.repo, output_path=os.path.join(out, "answer.md"),
                            receipt_path=os.path.join(out, "receipt.json"), timeout_s=timeout_s)


def main() -> int:
    stage = Stage()
    results = []

    def check(scene: str, claim: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {claim}" + (f"  ({detail})" if detail else ""))

    try:
        print("1. The registry builds lanes, and refuses what must not run")
        try:
            stage.lane("claude", "opus")
            refused = False
        except UsageError as error:
            refused = "native to parent" in str(error)
        check("1", "a same-provider lane is refused: the parent's native primitive owns it", refused)
        try:
            stage.lane("claude", "claude-opus-5-6", parent="codex")
            pinned = False
        except UsageError as error:
            pinned = "normalize it to opus" in str(error)
        check("1", "a versioned Claude pin must be normalized to its rolling alias first", pinned)

        print("2. Generated files come from the registry, and drift is a failure")
        catalog_dir = os.path.join(stage.root, "plugin")
        render.write(catalog_dir)
        with open(os.path.join(catalog_dir, render.CATALOG), "a") as f:
            f.write("a hand edit\n")
        check("2", "a hand edit to the generated catalog is reported with the write command", any("--write" in p for p in render.drift(catalog_dir)))

        print("3. A lane counts only with model proof")
        stage.control(claude={"reported": "claude-opus-5-5", "text": "LGTM"})
        code, receipt = run_lane(stage.lane("claude", "opus", parent="codex"), source_env=stage.env)
        check("3", "opus is proven by a provider-reported claude-opus revision", code == 0 and receipt["modelEvidence"] == "provider-report",
              f"reportedModel={receipt['reportedModel']}")
        stage.control(claude={"reported": "claude-sonnet-5-5"})
        code, receipt = run_lane(stage.lane("claude", "opus", parent="codex"), source_env=stage.env)
        check("3", "a different served model is malformed-output, and no answer file is left", code == 65 and not os.path.exists(receipt["outputPath"]))

        print("4. A dropout is not a fallback")
        stage.control(grok={"models": ["grok-4.6"]})
        code, receipt = run_lane(stage.lane("grok", "grok-4.7", "xhigh"), source_env=stage.env)
        check("4", "an unlisted model stops at the preflight; the model never runs", code == 69 and len(stage.calls()) == 1,
              receipt["status"])

        print("5. The child gets an environment without the parent's identity")
        stage.control(codex={})
        run_lane(stage.lane("codex", "gpt-6-luna", "low"), source_env=stage.env)
        env = stage.calls()[-1]["env"]
        check("5", "CLAUDECODE and the parent session id never reach the child", "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env)

        print("6. --timeout is one absolute deadline")
        stage.control(claude={"preflight_delay": 0.5, "delay": 5})
        started = time.time()
        code, receipt = run_lane(stage.lane("claude", "opus", parent="codex", timeout_s=1.0), source_env=stage.env, started=started)
        check("6", "preflight time counts against the same deadline as the model call", code == 124 and receipt["preflight"]["status"] == "passed",
              f"{time.time() - started:.1f}s")

        print("7. A panel fans out together, waits for every lane, and records dropouts")
        stage.control(codex={"exit": 1, "stderr": "The 'gpt-6.1-sol' model is not supported"}, grok={"text": "FINDING L3: off by one"})
        seen = []
        lanes = fan_out("claude", ["claude:opus@max", "codex:gpt-6.1-sol@max", "grok:grok-4.7@xhigh"], "review", os.path.join(stage.root, "panel"),
                        native=lambda a: "FINDING L3: boundary bug", cwd=stage.repo, source_env=stage.env,
                        on_all_terminal=lambda rs: seen.append([r.status for r in rs]))
        check("7", "two lanes count, the Sol lane is a named dropout, and the judge ran once after all three",
              [r.counted for r in lanes] == [True, False, True] and seen == [["complete", "unavailable-model", "complete"]])

        print("8. Setup probes before it writes, and a rerun changes nothing")
        home = os.path.join(stage.root, "home")
        os.makedirs(home)
        failed = sheet.setup(home, probe=lambda pair: not pair.startswith("grok:"))
        first = sheet.setup(home, probe=lambda pair: True)
        again = sheet.setup(home, probe=lambda pair: True)
        check("8", "a failed probe writes nothing; the second successful run is byte-identical",
              failed["written"] is False and first["written"] and again["unchanged"])

        print("9. The program store outlives the session; a new head voids its verdict")
        store = orch.Store(os.path.join(stage.root, "orchestrate"))
        store.init()
        store.unit_set("u1", state="verify", pr="7", sha="a1")
        store.ledger_record("7", "a1", "unit-test-verified", "pytest", "other-model")
        store.unit_set("u1", sha="b2")
        check("9", "after a restack the ledger answers 'unverified' for the new head", store.ledger_check("7", "b2") == "unverified")

        print("10. Context holds a mandate and skill names, not bodies")
        tree_root = os.path.join(stage.root, "skills")
        for name, front, body in (("poteto-mode", "description: default entry point\n", "THE FULL WORKFLOW"),
                                  ("principle-prove-it-works", "description: verify\nuser-invocable: false\n", "LEAF")):
            os.makedirs(os.path.join(tree_root, name))
            open(os.path.join(tree_root, name, "SKILL.md"), "w").write(f"---\nname: {name}\n{front}---\n{body}\n")
        tree = skills.load_tree(tree_root)
        context = skills.startup_context(tree, "You have pstack. Invoke pstack:poteto-mode for non-trivial work.")
        check("10", "the workflow body and the principle leaf stay out until they are read",
              "THE FULL WORKFLOW" not in context and "principle" not in context and "pstack:poteto-mode" in context)
    finally:
        shutil.rmtree(stage.root, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} checks PASS")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
