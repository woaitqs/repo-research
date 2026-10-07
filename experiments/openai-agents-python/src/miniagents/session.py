"""Sessions: client-side conversation memory as an append-only item log.

Upstream: the `Session` protocol (`src/agents/memory/session.py`) is four methods:
`get_items(limit)`, `add_items`, `pop_item`, `clear_session`. The runner prepends stored
history to the new input (`prepare_input_with_session`,
`src/agents/run_internal/session_persistence.py:414`) and appends each turn's items after
the turn (`save_result_to_session`). `SessionSettings.limit` keeps only the newest N items.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from .items import Item


@dataclass
class SessionSettings:
    limit: int | None = None


class Session(Protocol):
    session_id: str
    settings: SessionSettings

    async def get_items(self, limit: int | None = None) -> list[Item]: ...

    async def add_items(self, items: list[Item]) -> None: ...

    async def pop_item(self) -> Item | None: ...

    async def clear_session(self) -> None: ...


class InMemorySession:
    def __init__(self, session_id: str, settings: SessionSettings | None = None) -> None:
        self.session_id = session_id
        self.settings = settings or SessionSettings()
        self._items: list[Item] = []

    async def get_items(self, limit: int | None = None) -> list[Item]:
        items = [dict(i) for i in self._items]
        return items[-limit:] if limit else items

    async def add_items(self, items: list[Item]) -> None:
        self._items.extend(dict(i) for i in items)

    async def pop_item(self) -> Item | None:
        return self._items.pop() if self._items else None

    async def clear_session(self) -> None:
        self._items.clear()


class SQLiteSession:
    """Durable variant (stdlib sqlite3); one row per item, ordered by insertion."""

    def __init__(self, session_id: str, db_path: str = ":memory:", settings: SessionSettings | None = None):
        self.session_id = session_id
        self.settings = settings or SessionSettings()
        self._db = sqlite3.connect(db_path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " session_id TEXT, data TEXT)"
        )

    async def get_items(self, limit: int | None = None) -> list[Item]:
        rows = self._db.execute(
            "SELECT data FROM items WHERE session_id=? ORDER BY id", (self.session_id,)
        ).fetchall()
        items = [json.loads(r[0]) for r in rows]
        return items[-limit:] if limit else items

    async def add_items(self, items: list[Item]) -> None:
        self._db.executemany(
            "INSERT INTO items (session_id, data) VALUES (?, ?)",
            [(self.session_id, json.dumps(i, ensure_ascii=False)) for i in items],
        )
        self._db.commit()

    async def pop_item(self) -> Item | None:
        row = self._db.execute(
            "SELECT id, data FROM items WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        if row is None:
            return None
        self._db.execute("DELETE FROM items WHERE id=?", (row[0],))
        self._db.commit()
        return json.loads(row[1])

    async def clear_session(self) -> None:
        self._db.execute("DELETE FROM items WHERE session_id=?", (self.session_id,))
        self._db.commit()
