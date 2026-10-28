"""Do the tests guard the architecture? Inject one regression at a time.

Each mutation copies `src/` and `tests/` to a temp dir, applies one textual change that breaks an
architectural invariant, and runs the test suite against the copy. A mutation must make at least
one test fail ("killed"); a surviving mutation means the invariant is untested.

    python3 mutation_check.py      (uses .venv/bin/python if present)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, ".venv", "bin", "python")
PY = PY if os.path.exists(PY) else sys.executable

MUTATIONS = [
    ("a same-provider lane is launched externally", "minipstack/registry.py",
     "    if provider == parent:\n", "    if False:\n"),
    ("a versioned Claude pin runs without normalization", "minipstack/registry.py",
     '    if kind == "stale-revision":\n', "    if False:\n"),
    ("the parent's identity markers reach the child", "minipstack/runner.py",
     '    for key in PARENTS[lane.parent]["identity"]:', "    for key in ():"),
    ("an allowlist provider inherits the whole environment", "minipstack/adapters.py",
     "        return {k: v for k, v in source.items() if k in BASE_ALLOWLIST or k.startswith(prefixes)}",
     "        return dict(source)"),
    ("an existing receipt is truncated instead of refused", "minipstack/runner.py",
     "os.O_CREAT | os.O_EXCL | os.O_WRONLY", "os.O_CREAT | os.O_TRUNC | os.O_WRONLY"),
    ("an answer counts without model proof", "minipstack/runner.py",
     "        if parsed.reported_model is not None and adapter.matches(lane, parsed.reported_model):",
     "        if True:"),
    ("an unlisted model still runs (preflight verdict ignored)", "minipstack/runner.py",
     '        if verdict != "passed":', '        if verdict not in ("passed", "unavailable-model"):'),
    ("a model-phase auth error becomes unauthenticated", "minipstack/runner.py",
     '            return finish(model_failure_status(raw), f"child exited with status {model.exit_code}", raw)',
     '            return finish("unauthenticated" if "authentication" in raw else model_failure_status(raw), "x", raw)'),
    ("each phase gets a fresh timeout allowance", "minipstack/runner.py",
     "                        deadline, cancel, (out_fd, err_fd))",
     "                        None if deadline is None else time.time() + lane.timeout_s, cancel, (out_fd, err_fd))"),
    ("cancellation stops waiting but never signals the child", "minipstack/runner.py",
     '                return "cancelled", self._terminate(signal.SIGTERM)', '                return "cancelled", None'),
    ("the Claude writer's shared .git deny is dropped", "minipstack/adapters.py",
     "            if shared_git_dir is not None:", "            if False:"),
    ("a failed lane still counts toward the panel", "minipstack/dispatch.py",
     '    counted = receipt["status"] == "complete" and proven', "    counted = True"),
    ("lanes run one after another", "minipstack/dispatch.py",
     "ThreadPoolExecutor(max_workers=max(1, len(assignments)))", "ThreadPoolExecutor(max_workers=1)"),
    ("a single model's finding is reported as agreement", "minipstack/dispatch.py",
     '"agreement": [f for f in findings if len(f["raised_by"]) >= 2]', '"agreement": [f for f in findings if len(f["raised_by"]) >= 1]'),
    ("setup writes even when a probe failed", "minipstack/sheet.py",
     "    if not all(probes.values()):", "    if False:"),
    ("setup leaves a half-written state when readback differs", "minipstack/sheet.py",
     "        for path, snapshot in snapshots.items():\n            _restore(path, snapshot)\n", "        pass\n"),
    ("the ledger answers for any head of the PR", "minipstack/orch.py",
     'if r["pr"] == pr and r["sha"] == head_sha), None)', 'if r["pr"] == pr), None)'),
    ("a lock held by a dead process blocks the store forever", "minipstack/orch.py",
     "                    if not (holder.isdigit() and not _alive(int(holder))) and not force:", "                    if not force:"),
    ("a hand edit of a generated file goes unnoticed", "minipstack/render.py",
     "        elif open(full).read() != content:", "        elif False:"),
    ("descriptors may appear in hand-written prose", "minipstack/render.py",
     '    patterns = [re.compile(rf"\\b(?:{providers}):[A-Za-z0-9._/-]+@[a-z]+")]', "    patterns = []"),
    ("principle leaves are listed to the model at startup", "minipstack/skills.py",
     'for s in tree.values() if s.user_invocable]', "for s in tree.values()]"),
]


def main() -> int:
    survivors = 0
    for label, rel, old, new in MUTATIONS:
        tmp = tempfile.mkdtemp(prefix="minipstack-mut-")
        try:
            shutil.copytree(os.path.join(HERE, "src"), os.path.join(tmp, "src"))
            shutil.copytree(os.path.join(HERE, "tests"), os.path.join(tmp, "tests"))
            path = os.path.join(tmp, "src", rel)
            text = open(path).read()
            if text.count(old) != 1:
                print(f"  BROKEN  {label}: pattern found {text.count(old)} times in {rel}")
                survivors += 1
                continue
            open(path, "w").write(text.replace(old, new))
            run = subprocess.run([PY, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", "tests"], cwd=tmp,
                                 capture_output=True, text=True, env={**os.environ, "PYTHONPATH": os.path.join(tmp, "src")})
            killed = run.returncode != 0
            survivors += not killed
            failed = [line.split("::")[-1].split(" - ")[0] for line in run.stdout.splitlines() if line.startswith("FAILED")]
            print(f"  {'killed ' if killed else 'SURVIVED'} {label}" + (f"  <- {', '.join(failed)}" if failed else ""))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(MUTATIONS) - survivors}/{len(MUTATIONS)} architectural regressions killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
