"""Rolling summarizing condenser.

Mirrors openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:
* Triggers on an unhandled CondensationRequest (hard) or when the view has
  more than `max_size` events (soft).
* Keeps the system prompt and the first `keep_first` events, keeps a tail of
  `max_size // 2 - keep_first - 1` events, and forgets the middle.
* Cut points snap to View.manipulation_indices, so a tool call is never
  separated from its observation.
* The previous summary sits inside the forgotten range, so the next summary
  rolls it up (the SDK prompt says "events ... will include previous summaries").
* Returns a Condensation *event*; the agent emits it and returns, and the next
  step sees the condensed view. The full history stays on disk.
"""

from __future__ import annotations

from .events import Condensation, Event
from .llm import LLM
from .view import View

SUMMARY_SYSTEM_PROMPT = (
    "You maintain a state summary for an agent. Summarize the events below, "
    "including any previous summary. Keep: USER_CONTEXT, COMPLETED, PENDING, "
    "CODE_STATE (files touched), CURRENT_STATE."
)


class NoCondensationAvailable(Exception):
    pass


class SummarizingCondenser:
    def __init__(self, llm: LLM, max_size: int = 12, keep_first: int = 2) -> None:
        if max_size // 2 - keep_first - 1 <= 0:
            raise ValueError("keep_first must be less than max_size // 2")
        self.llm = llm
        self.max_size = max_size
        self.keep_first = keep_first

    def condense(self, view: View) -> View | Condensation:
        hard = view.unhandled_condensation_request
        if not hard and len(view) <= self.max_size:
            return view
        try:
            return self._condensation(view, hard)
        except NoCondensationAvailable:
            if hard:
                raise
            return view  # soft requirement: try again on a later step

    def _condensation(self, view: View, hard: bool) -> Condensation:
        target = len(view) // 2 if hard else self.max_size // 2
        keep_tail = max(target - self.keep_first - 1, 1)
        protected = self.keep_first
        sys_idx = view.system_prompt_index()
        if sys_idx is not None:
            protected = max(protected, sys_idx + 1)
        start = view.next_manipulation_index(protected)
        end = view.next_manipulation_index(len(view) - keep_tail)
        forgotten = view.events[start:end]
        if not forgotten:
            raise NoCondensationAvailable("no safe range to forget")
        summary = self._summarize(forgotten)
        return Condensation(
            forgotten_event_ids=[e.id for e in forgotten],
            summary=summary,
            summary_offset=start,
        )

    def _summarize(self, events: list[Event]) -> str:
        lines = [f"- {type(e).__name__}: {str(e.to_llm_message())[:300]}" for e in events]
        response = self.llm.complete(
            [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)},
            ]
        )
        return response.text
