"""Do the tests guard the architecture? Inject one regression at a time."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, ".venv", "bin", "python")
PY = PY if os.path.exists(PY) else sys.executable

MUTATIONS = [
    (
        "same-day turn refreshes the memory reminder",
        "minideer/context.py",
        "if last_date == today:\n            return None  # FROZEN_SNAPSHOT",
        "if False:\n            return None",
    ),
    (
        "summarization keeps the compacted messages",
        "minideer/context.py",
        'state["messages"] = kept  # DROP_COMPACTED',
        "state['summary_text'] = body.strip()  # mutated, messages kept",
    ),
    (
        "durable context is written back onto the thread",
        "minideer/context.py",
        "return handler(request.override(messages=messages))  # REQUEST_ONLY_DURABLE",
        "request.state['messages'] = messages\n        return handler(request.override(messages=messages))",
    ),
    (
        "child starts from the parent transcript",
        "minideer/subagents.py",
        '"messages": [HumanMessage(prompt)],  # ISOLATED_CHILD',
        '"messages": list(parent["messages"]) + [HumanMessage(prompt)],',
    ),
    (
        "non-interactive approval pauses instead of denying",
        "minideer/hitl.py",
        "if not self.supports_resume or not ctx.interactive:",
        "if False:",
    ),
    (
        "slash skill body is re-injected on every model call",
        "minideer/context.py",
        "return handler(request)  # SLASH_ONCE",
        "pass  # mutated",
    ),
    (
        "sandbox is acquired before any tool call",
        "minideer/sandbox.py",
        "return None  # LAZY_ACQUIRE",
        "state['sandbox'] = {'sandbox_id': self.provider.acquire(ctx.thread_id)}",
    ),
    (
        "clarification continues the turn",
        "minideer/hitl.py",
        "raise EndTurn(message)  # CLARIFICATION_ENDS",
        "return message",
    ),
]


def main() -> int:
    survivors = 0
    for label, rel, old, new in MUTATIONS:
        tmp = tempfile.mkdtemp(prefix="minideer-mut-")
        shutil.copytree(os.path.join(HERE, "src"), os.path.join(tmp, "src"))
        shutil.copytree(os.path.join(HERE, "tests"), os.path.join(tmp, "tests"))
        path = os.path.join(tmp, "src", rel)
        text = open(path).read()
        if old not in text:
            print(f"ANCHOR MISSING  {label}")
            survivors += 1
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        open(path, "w").write(text.replace(old, new, 1))
        proc = subprocess.run(
            [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
            cwd=tmp,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": os.path.join(tmp, "src")},
        )
        failed = re.search(r"(\d+) failed", proc.stdout)
        n = int(failed.group(1)) if failed else 0
        killed = proc.returncode != 0
        survivors += not killed
        print(f"{'KILLED ' if killed else 'SURVIVED'}  {n:>2} failing  {label}")
        if not killed:
            print(proc.stdout[-400:])
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(MUTATIONS) - survivors}/{len(MUTATIONS)} mutations killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
