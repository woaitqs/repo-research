"""Do the tests guard the architecture? Inject one regression at a time.

Each mutation copies `src/` to a temp dir, applies one textual change that breaks
an architectural invariant, and runs the test suite against the copy. A mutation
must make at least one test fail ("killed"); a surviving mutation means the
invariant is untested.

    python3 mutation_check.py      (uses .venv/bin/python if present)
"""

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
    ("memory change rewrites the system prompt (no cache-stable delta)", "minilc/backend.py",
     'return cached["content"], memfs.memory_update_delta(fresh["core_memory"], fresh["revision"])',
     'return self.recompile(conversation_id), None'),
    ("memory compiled from the working tree instead of HEAD", "minilc/memfs.py",
     'meta, body = _frontmatter(_git(memory_dir, "show", f"HEAD:{rel}"))',
     'meta, body = _frontmatter(open(os.path.join(memory_dir, rel)).read())'),
    ("compaction rewrites the transcript file (destructive)", "minilc/transcript.py",
     '        self.messages[summary_message["id"]] = summary_message\n',
     '        self.messages[summary_message["id"]] = summary_message\n        open(self.path, "w").close()\n'),
    ("sliding window may cut between a tool call and its result", "minilc/compaction.py",
     'if messages[i]["role"] == "assistant" and i < max_cut), None)',
     'if i < max_cut), None)'),
    ("no settlement of dangling tool calls", "minilc/backend.py",
     'if not any(m.get("type") == "approval" for m in messages):',
     'if False:'),
    ("volatile reminders written into the system prompt", "minilc/backend.py",
     'return {"system": system, "messages": view, "tools": body.get("client_tools") or []}',
     'return {"system": system + json.dumps(body.get("messages", [])[:1]), "messages": view, "tools": body.get("client_tools") or []}'),
    ("fresh sub-agent inherits the parent conversation", "minilc/subagents.py",
     'if subagent_type == "fork" and parent_conversation_id:',
     'if parent_conversation_id:'),
    ("shell tools run in parallel (no global lock)", "minilc/tools.py",
     'PARALLEL_SAFE = {"Read", "Grep", "Glob", "Agent"}',
     'PARALLEL_SAFE = {"Read", "Grep", "Glob", "Agent", "Bash"}'),
    ("headless 'ask' silently executes instead of denying", "minilc/harness.py",
     'if self.ask_user is not None and self.ask_user(call):',
     'if True:'),
]


def main() -> int:
    survivors = 0
    for label, rel, old, new in MUTATIONS:
        tmp = tempfile.mkdtemp(prefix="minilc-mut-")
        shutil.copytree(os.path.join(HERE, "src"), os.path.join(tmp, "src"))
        shutil.copytree(os.path.join(HERE, "tests"), os.path.join(tmp, "tests"))
        path = os.path.join(tmp, "src", rel)
        text = open(path).read()
        assert old in text, f"mutation anchor not found: {label}"
        open(path, "w").write(text.replace(old, new, 1))
        proc = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=tmp,
                              capture_output=True, text=True, env={**os.environ, "PYTHONPATH": os.path.join(tmp, "src")})
        failed = re.search(r"(\d+) failed", proc.stdout)
        n = int(failed.group(1)) if failed else 0
        killed = proc.returncode != 0
        survivors += not killed
        print(f"{'KILLED ' if killed else 'SURVIVED'}  {n:>2} failing  {label}")
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(MUTATIONS) - survivors}/{len(MUTATIONS)} mutations killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
