"""Do the tests test the architecture? Inject one architectural regression at a time.

    python3 mutation_check.py        # needs pytest on the current interpreter

Each mutation edits one source file in place, runs the suite, records how many tests fail,
and restores the file. A mutation that no test catches is printed as SURVIVED.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src" / "miniagents"

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "handoff nests history by default",
        "run.py",
        "nest_handoff_history: bool = False",
        "nest_handoff_history: bool = True",
    ),
    (
        "tool exceptions crash the run",
        "tool.py",
        "            if self.failure_error_function is None:\n                raise",
        "            raise",
    ),
    (
        "approval check skipped",
        "run.py",
        "if decision is None and run.tool.requires_approval(ctx, run.call[\"arguments\"]):",
        "if False:",
    ),
    (
        "resume re-runs the model instead of finishing the paused turn",
        "run.py",
        "            if state.current_step is not None:\n                # Resuming",
        "            if False:\n                # Resuming",
    ),
    (
        "paused turn persisted immediately",
        "run.py",
        "                state.turn_start = len(turn.pre_step_items)\n",
        "                state.turn_start = len(turn.pre_step_items)\n"
        "                if session is not None:\n"
        "                    await session.add_items([i for i in (r.to_input() for r in turn_session_items) if i])\n",
    ),
    (
        "session history not prepended",
        "run.py",
        "original_input=history + new_input if session is not None else input,",
        "original_input=new_input if session is not None else input,",
    ),
    (
        "capabilities mutate the public agent",
        "capabilities.py",
        "        capabilities = [c.clone() for c in agent.capabilities]",
        "        capabilities = agent.capabilities",
    ),
    (
        "tools run sequentially",
        "run.py",
        "    results = await asyncio.gather(*(r.tool.invoke(ctx, r.call[\"arguments\"]) for r in to_execute))",
        "    results = [await r.tool.invoke(ctx, r.call[\"arguments\"]) for r in to_execute]",
    ),
    (
        "tool_choice never reset",
        "run.py",
        "        settings.pop(\"tool_choice\", None)",
        "        pass",
    ),
    (
        "input guardrails run on every turn and agent",
        "run.py",
        "                if state.current_turn == 1 and not resuming:",
        "                if True:",
    ),
    (
        "sub-agent inherits the parent transcript",
        "agent.py",
        "self, args[\"input\"], context=ctx.context",
        "self, [*_parent_items(ctx), {\"role\": \"user\", \"content\": args[\"input\"]}], context=ctx.context",
    ),
]

# Helper injected for the last mutation: the parent's turn input, made visible to the tool.
PARENT_ITEMS_HELPER = '''

def _parent_items(ctx):
    return list(getattr(ctx, "_parent_input", []))
'''


def run_tests() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-200:]
    failed = re.search(r"(\d+) failed", tail)
    return (int(failed.group(1)) if failed else 0), tail


def main() -> int:
    baseline_failed, baseline = run_tests()
    print(f"baseline: {baseline}")
    if baseline_failed:
        return 1
    survived = 0
    for name, file, old, new in MUTATIONS:
        path = SRC / file
        original = path.read_text()
        if old not in original:
            print(f"[SKIP] {name}: anchor not found")
            survived += 1
            continue
        mutated = original.replace(old, new, 1)
        if "_parent_items" in new:
            mutated += PARENT_ITEMS_HELPER
            run_path = SRC / "run.py"
            run_original = run_path.read_text()
            # Expose the parent's model input on the context so the mutant can leak it.
            run_path.write_text(
                run_original.replace(
                    "    model_input = prepare_model_input(state.original_input, state.generated_items)\n",
                    "    model_input = prepare_model_input(state.original_input, state.generated_items)\n"
                    "    ctx._parent_input = model_input\n",
                    1,
                )
            )
        path.write_text(mutated)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            tail = proc.stdout.strip().splitlines()[-1]
            failed = re.search(r"(\d+) failed", tail)
            count = int(failed.group(1)) if failed else 0
        finally:
            path.write_text(original)
            if "_parent_items" in new:
                (SRC / "run.py").write_text(run_original)
        status = "KILLED" if count else "SURVIVED"
        survived += 0 if count else 1
        print(f"[{status}] {name}: {count} test(s) failed")
    after_failed, after = run_tests()
    print(f"after restore: {after}")
    return 0 if survived == 0 and after_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
