"""Human scores on two saved runs. Saving one does not call a model."""

from __future__ import annotations

from typing import Any


def save_evaluation(
    thread: dict[str, Any],
    *,
    left_run_id: str,
    right_run_id: str,
    rubric: dict[str, Any],
    scores: dict[str, dict[str, int]],
    verdict: str,
) -> dict[str, Any]:
    if verdict not in ("left", "right", "tie", "unsure"):
        raise ValueError(verdict)
    for run_id, by_criterion in scores.items():
        for score in by_criterion.values():
            if not isinstance(score, int) or score < 1 or score > 5:
                raise ValueError(score)
    record = {
        "left_run_id": left_run_id,
        "right_run_id": right_run_id,
        "rubric": {
            "id": rubric["id"],
            "revision": rubric["revision"],
            "criteria": list(rubric["criteria"]),
        },
        "scores": {run_id: dict(by_criterion) for run_id, by_criterion in scores.items()},
        "verdict": verdict,
    }
    thread.setdefault("evaluations", []).append(record)  # EVAL_NO_MODEL
    return record
