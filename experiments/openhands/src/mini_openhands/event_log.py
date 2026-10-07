"""Append-only, file-backed event log.

Mirrors openhands-sdk/openhands/sdk/conversation/event_store.py (EventLog) and
persistence_const.py: one JSON file per event named
`events/event-{idx:05d}-{event_id}.json`. Appends are cheap and crash-safe
(write temp file, then atomic rename); a reader can rebuild everything by
scanning the directory in index order.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

from .events import Event

EVENT_NAME_RE = re.compile(r"^event-(?P<idx>\d{5,})-(?P<event_id>[0-9a-zA-Z\-]+)\.json$")


class EventLog:
    def __init__(self, directory: Path | None) -> None:
        self._dir = directory
        self._events: list[Event] = []
        self._ids: set[str] = set()
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self) -> None:
        assert self._dir is not None
        entries = []
        for path in self._dir.iterdir():
            match = EVENT_NAME_RE.match(path.name)
            if match:
                entries.append((int(match.group("idx")), path))
        for _, path in sorted(entries):
            event = Event.from_dict(json.loads(path.read_text(encoding="utf-8")))
            self._events.append(event)
            self._ids.add(event.id)

    def append(self, event: Event) -> int:
        if event.id in self._ids:
            raise ValueError(f"duplicate event id {event.id}")
        idx = len(self._events)
        if self._dir is not None:
            final = self._dir / f"event-{idx:05d}-{event.id}.json"
            tmp = final.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(event.to_dict(), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, final)
        self._events.append(event)
        self._ids.add(event.id)
        return idx

    def __len__(self) -> int:
        return len(self._events)

    def __getitem__(self, idx: int) -> Event:
        return self._events[idx]

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)
