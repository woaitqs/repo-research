"""Workspace files stay user content. Run bodies live in a side store."""

from __future__ import annotations

import hashlib
from typing import Any

ORPHAN_RETENTION_MS = 30 * 24 * 60 * 60 * 1000


def _ref(run_id: str) -> str:
    return hashlib.sha256(run_id.encode()).hexdigest() + ".json"


class Home:
    def __init__(self) -> None:
        self.workspace: dict[str, dict[str, Any]] = {}
        self.history: dict[str, dict[str, dict[str, Any]]] = {}
        self.orphaned_at: dict[str, int] = {}

    def save(self, path: str, thread: dict[str, Any]) -> None:
        runs = list(thread.get("runs") or [])
        folder = self.history.setdefault(path, {})
        refs: list[str] = []
        for run in runs:
            ref = _ref(run["id"])
            folder[ref] = run
            refs.append(ref)
        stored = {key: value for key, value in thread.items() if key != "runs"}
        stored["run_index"] = refs  # EXTERNALIZE
        self.workspace[path] = stored
        self.orphaned_at.pop(path, None)

    def read_run(self, path: str, ref: str) -> dict[str, Any]:
        return self.history[path][ref]

    def delete(self, path: str, now: int) -> None:
        self.workspace.pop(path, None)
        if path in self.history:
            self.orphaned_at[path] = now

    def maintain(self, now: int) -> list[str]:
        reclaimed: list[str] = []
        for path, stamped in list(self.orphaned_at.items()):
            if now - stamped > ORPHAN_RETENTION_MS:
                self.history.pop(path, None)
                del self.orphaned_at[path]
                reclaimed.append(path)
        return reclaimed
