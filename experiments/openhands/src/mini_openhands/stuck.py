"""Stuck detection over the event log.

Mirrors openhands-sdk/openhands/sdk/conversation/stuck_detector.py
(action_observation threshold = 4 by default): if, since the last user
message, the same action produced the same observation N times in a row, the
run loop sets STUCK instead of burning more model calls.
"""

from __future__ import annotations

from .events import ActionEvent, Event, MessageEvent, ObservationEvent


def is_stuck(events: list[Event], threshold: int = 4) -> bool:
    tail: list[Event] = []
    for event in reversed(events):
        if isinstance(event, MessageEvent) and event.source == "user":
            break
        tail.append(event)
    tail.reverse()
    pairs: list[tuple[str, str]] = []
    pending: dict[str, ActionEvent] = {}
    for event in tail:
        if isinstance(event, ActionEvent):
            pending[event.id] = event
        elif isinstance(event, ObservationEvent) and event.action_id in pending:
            action = pending.pop(event.action_id)
            pairs.append((f"{action.tool_name}:{action.raw_arguments}", event.content))
    if len(pairs) < threshold:
        return False
    last = pairs[-threshold:]
    return all(p == last[0] for p in last)
