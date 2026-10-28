"""Parent-owned routing and fan-out, with dropouts instead of fallbacks.

Mirrors `provider-dispatch.md` and the panel skills (`interrogate`, `arena`, `swarm`). The top-level
parent resolves each descriptor's route once: a provider native to the parent uses the parent's own
subagent primitive, every other provider uses the external runner. A child receives a finished
assignment and has no way to pick a route. All lanes start in one fan-out phase and the parent
waits for every lane to be terminal before anything reads or judges them. A lane counts only with
a complete, model-proven result. A failed lane is a named dropout and is never replaced.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .registry import UsageError, resolve_lane
from .runner import run_lane

ALIASES = ("inherit-parent", "auto")


@dataclass(frozen=True)
class Assignment:
    """Everything a child gets. There is no registry, sheet, or parent identity in it to route with."""

    label: str
    provider: str
    model: str
    effort: str
    mode: str
    prompt: str
    output_dir: str


@dataclass
class LaneResult:
    label: str
    descriptor: str
    route: str
    status: str
    counted: bool
    answer: str | None
    receipt: dict | None
    reason: str | None = None


def route(parent: str, value: str) -> tuple[str, str, str, str]:
    if value in ALIASES:
        return "native", parent, value, "inherit"
    match = re.fullmatch(r"([a-z]+):([A-Za-z0-9._/-]+)@([a-z]+)", value)
    if match is None:
        raise UsageError(f"{value} is not a provider-qualified descriptor")
    provider, model, effort = match.groups()
    return ("native" if provider == parent else "external"), provider, model, effort


def _external(parent: str, a: Assignment, cwd: str, source_env: dict | None) -> LaneResult:
    prompt_path = os.path.join(a.output_dir, "prompt.md")
    with open(prompt_path, "w") as f:
        f.write(a.prompt)
    descriptor = f"{a.provider}:{a.model}@{a.effort}"
    try:
        lane = resolve_lane(parent=parent, provider=a.provider, model=a.model, effort=a.effort, mode=a.mode,
                            prompt_path=prompt_path, cwd=cwd, output_path=os.path.join(a.output_dir, "answer.md"),
                            receipt_path=os.path.join(a.output_dir, "receipt.json"))
    except UsageError as error:
        return LaneResult(a.label, descriptor, "external", "refused", False, None, None, str(error))
    _, receipt = run_lane(lane, source_env=source_env)
    proven = receipt["modelVerified"] or receipt["modelEvidence"] == "pinned-argv"
    counted = receipt["status"] == "complete" and proven
    answer = open(lane.output_path).read() if counted else None
    reason = None if counted else (receipt.get("error") or {}).get("message", receipt["status"])
    return LaneResult(a.label, descriptor, "external", receipt["status"], counted, answer, receipt, reason)


def fan_out(parent: str, descriptors: list[str], prompt: str, workdir: str, *, native, mode: str = "read-only",
            cwd: str | None = None, source_env: dict | None = None, on_all_terminal=None) -> list[LaneResult]:
    """Start every lane together, then return only after every lane is terminal."""
    assignments, routes = [], []
    for index, value in enumerate(descriptors):
        kind, provider, model, effort = route(parent, value)
        out = os.path.join(workdir, f"lane-{index + 1}")
        os.makedirs(out, exist_ok=False)
        assignments.append(Assignment(chr(ord("A") + index), provider, model, effort, mode, prompt, out))
        routes.append((kind, value))

    def run(pair):
        (kind, value), a = pair
        if kind == "native":
            try:
                answer = native(a)
            except Exception as error:
                return LaneResult(a.label, value, "native", "child-failed", False, None, None, repr(error))
            return LaneResult(a.label, value, "native", "complete", answer is not None, answer, None)
        return _external(parent, a, cwd or workdir, source_env)

    with ThreadPoolExecutor(max_workers=max(1, len(assignments))) as pool:
        results = list(pool.map(run, zip(routes, assignments)))
    if on_all_terminal is not None:
        on_all_terminal(results)
    return results


FINDING = re.compile(r"^\s*FINDING\s+L(\d+)\s*:\s*(.+)$", re.M)


def interrogate(parent: str, reviewers: list[str], intent: str, diff: str, workdir: str, *, native,
                cwd: str | None = None, source_env: dict | None = None) -> dict:
    """One reviewer per configured descriptor; merge by cited line; agreement = raised by 2+ models."""
    prompt = (f"Intent: {intent}\n\nReview this change adversarially. For each real defect write one line exactly as\n"
              f"FINDING L<line number>: <one sentence>\nand nothing else. If there is no defect, write NO FINDINGS.\n\n{diff}")
    results = fan_out(parent, reviewers, prompt, workdir, native=native, cwd=cwd, source_env=source_env)
    by_line: dict[int, dict] = {}
    for result in results:
        if not result.counted:
            continue
        for line, text in FINDING.findall(result.answer or ""):
            entry = by_line.setdefault(int(line), {"line": int(line), "raised_by": [], "texts": []})
            if result.descriptor not in entry["raised_by"]:
                entry["raised_by"].append(result.descriptor)
                entry["texts"].append(text.strip())
    findings = sorted(by_line.values(), key=lambda f: (-len(f["raised_by"]), f["line"]))
    return {
        "intent": intent,
        "reviewers": [{"label": r.label, "descriptor": r.descriptor, "route": r.route, "status": r.status,
                       "counted": r.counted, "reason": r.reason} for r in results],
        "dropouts": [r.descriptor for r in results if not r.counted],
        "agreement": [f for f in findings if len(f["raised_by"]) >= 2],
        "single": [f for f in findings if len(f["raised_by"]) == 1],
    }
