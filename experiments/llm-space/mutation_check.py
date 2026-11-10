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
        "snapshot no longer wins over a new value",
        "minispace/render.py",
        'if "system" in snapshot:  # SNAPSHOT_WINS',
        "if False:  # SNAPSHOT_WINS",
    ),
    (
        "render writes the resolved system prompt back",
        "minispace/render.py",
        "return request, snapshot  # TEMPLATE_UNCHANGED",
        'thread["system"] = system_rendered\n    return request, snapshot',
    ),
    (
        "pi executes the tool inside the model step",
        "minispace/loop.py",
        "# TERMINATE_PI: do not run the tools. The workbench owns that.",
        "self.executed.append('tool')",
    ),
    (
        "the react cap allows extra model turns",
        "minispace/workbench.py",
        "for _turn in range(MAX_AUTO_TOOL_TURNS):",
        "for _turn in range(MAX_AUTO_TOOL_TURNS + 5):  # REACT_CAP",
    ),
    (
        "a function tool is executed instead of stopping",
        "minispace/workbench.py",
        'return None, "human", []  # HUMAN_FUNCTION',
        "planned.append((call, tool))",
    ),
    (
        "run bodies stay inside the workspace file",
        "minispace/storage.py",
        'stored["run_index"] = refs  # EXTERNALIZE',
        'stored["run_index"] = refs\n        stored["runs"] = runs',
    ),
    (
        "the child starts from the parent transcript",
        "minispace/subagent.py",
        '"messages": messages,  # CHILD_ISOLATED',
        '"messages": list(parent.get("messages") or []) + messages,',
    ),
    (
        "saving an evaluation counts as a model call",
        "minispace/evaluation.py",
        'thread.setdefault("evaluations", []).append(record)  # EVAL_NO_MODEL',
        'thread["model_calls"] = thread.get("model_calls", 0) + 1\n    thread.setdefault("evaluations", []).append(record)',
    ),
    (
        "compaction rewrites the source message list",
        "minispace/compaction.py",
        "# COMPACT_COPY: the caller's message list is left as it was.",
        "messages_holder = plan.get('_source')",
    ),
]


def main() -> int:
    survivors = 0
    for label, rel, old, new in MUTATIONS:
        tmp = tempfile.mkdtemp(prefix="minispace-mut-")
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
        # The compaction mutation above is a no-op unless the test watches the
        # source list. Pair it with an in-place rewrite so the regression is real.
        if "COMPACT_COPY" in old:
            rewritten = open(path).read().replace(
                "applied.extend(plan[\"kept\"])\n    return applied",
                "plan['kept'][:] = []\n    return applied",
                1,
            )
            open(path, "w").write(rewritten)
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
            print(proc.stdout[-500:])
            print(proc.stderr[-500:])
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(MUTATIONS) - survivors}/{len(MUTATIONS)} mutations killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
