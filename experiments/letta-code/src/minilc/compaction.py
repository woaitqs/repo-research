"""Context-pressure policy and compaction planning.

Mirrors `src/backend/dev/provider-turn-executor.ts:241-272` (threshold) and
`src/backend/local/compaction.ts` (planning, packaging):

* compact when estimated context > window - min(16_384, 20% of window);
* `sliding_window` (default) evicts from the front, starting at 30% and adding
  10% until the kept tail fits under (1 - 30%) of the window, and only cuts *at
  an assistant message* so a tool call is never separated from its result;
* `all` summarises everything except a trailing pending tool call;
* the summary re-enters context as a **user-role** `system_alert` JSON message.
"""

from __future__ import annotations

import json
import math
from typing import Any

RESERVE_TOKENS = 16_384
SMALL_WINDOW_RESERVE_RATIO = 0.2
DEFAULT_SLIDING_WINDOW_PERCENTAGE = 0.3


class PlanningError(Exception):
    """Sliding window could not find a safe cut; caller falls back to `all`."""


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return math.ceil(len(text) / 4)


def compaction_threshold(context_window: int) -> int:
    reserve = min(RESERVE_TOKENS, max(1, math.floor(context_window * SMALL_WINDOW_RESERVE_RATIO)))
    return max(0, context_window - reserve)


def should_compact(context_tokens: int, context_window: int) -> bool:
    return context_tokens > compaction_threshold(context_window)


def _has_tool_call(message: dict[str, Any]) -> bool:
    return message["role"] == "assistant" and bool(message.get("tool_calls"))


def plan_all(messages: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    if messages and _has_tool_call(messages[-1]):
        return messages[:-1], messages[-1:]
    return messages, []


def plan_sliding_window(messages: list[dict[str, Any]], context_window: int,
                        percentage: float = DEFAULT_SLIDING_WINDOW_PERCENTAGE) -> tuple[list[dict], list[dict]]:
    if len(messages) < 4:
        raise PlanningError("not enough messages")
    max_cut = len(messages) - 2 if _has_tool_call(messages[-1]) else len(messages) - 1
    goal = (1 - percentage) * context_window
    approx = context_window
    cut = None
    eviction = percentage
    while approx >= goal and eviction < 1.0:
        eviction += 0.1
        limit = min(round(eviction * len(messages)), len(messages) - 1)
        cut = next((i for i in range(limit, 0, -1)
                    if messages[i]["role"] == "assistant" and i < max_cut), None)
        if cut is None:
            continue
        approx = estimate_tokens(messages[cut:])
    if cut is None or eviction >= 1.0 or cut >= max_cut:
        raise PlanningError("no safe assistant boundary")
    return messages[:cut], messages[cut:]


def package_summary(summary: str, evicted: int, mode: str) -> str:
    if mode == "sliding_window":
        note = (f"Note: {evicted} messages from the beginning of the conversation have been hidden "
                "from view due to memory constraints.\nThe following is a summary of the previous messages:\n ")
    else:
        note = ("Note: prior messages have been hidden from view due to conversation memory constraints.\n"
                "The following is a summary of the previous messages:\n ")
    return json.dumps({"type": "system_alert", "message": note + summary})
