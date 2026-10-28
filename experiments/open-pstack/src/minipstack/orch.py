"""The Orchestrate store: a program's durable memory outside any session.

Mirrors `scripts/orch/store.ts` and `playbooks/orchestrate.md`. The store lives outside the repo
and the session, so a restart loses running agents but not the program. Every file has one
writer, every write is atomic, and a lock held by a dead process is taken over. Completions are
queue events (`inbox`), not interrupts. Verification is a ledger row keyed by PR and head SHA, so
a new head voids the old verdict. Status is derived from the tables, never narrated.
"""

from __future__ import annotations

import errno
import json
import os
import time
import uuid

UNIT_HEADER = ("id", "track", "state", "branch", "pr", "sha", "brief")
LEDGER_HEADER = ("pr", "sha", "verdict", "evidence", "verifier", "ts")
VERDICTS = ("live-ui-verified", "unit-test-verified", "type-check-only", "verifier-blocked", "verifier-failed")
PASSING = ("live-ui-verified", "unit-test-verified")


class StoreError(RuntimeError):
    pass


def atomic_write(path: str, text: str) -> None:
    temporary = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temporary, "x") as f:
            f.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


class Store:
    def __init__(self, root: str):
        self.root = root

    def _path(self, name: str) -> str:
        return os.path.join(self.root, name)

    def init(self) -> None:
        os.makedirs(self._path("inbox"), exist_ok=True)
        for name, header in (("units.tsv", UNIT_HEADER), ("ledger.tsv", LEDGER_HEADER)):
            if not os.path.exists(self._path(name)):
                atomic_write(self._path(name), "\t".join(header) + "\n")
        for name in ("preferences.md", "gates.md"):
            if not os.path.exists(self._path(name)):
                atomic_write(self._path(name), "")

    def lock(self, force: bool = False):
        path = self._path(".orch.lock")
        store = self

        class _Lock:
            def __enter__(self):
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    holder = open(path).read().strip()
                    if not (holder.isdigit() and not _alive(int(holder))) and not force:
                        raise StoreError(f"store lock held by pid {holder}") from None
                    os.unlink(path)
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, f"{os.getpid()}\n".encode())
                os.close(fd)
                return store

            def __exit__(self, *exc):
                if os.path.exists(path) and open(path).read().strip() == str(os.getpid()):
                    os.unlink(path)

        return _Lock()

    def _rows(self, name: str) -> list[dict]:
        lines = open(self._path(name)).read().splitlines()
        header = lines[0].split("\t")
        return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line]

    def _save(self, name: str, header, rows: list[dict]) -> None:
        clean = lambda v: str(v).replace("\t", " ").replace("\n", " ")
        atomic_write(self._path(name), "\n".join(["\t".join(header)] + ["\t".join(clean(r.get(h, "")) for h in header) for r in rows]) + "\n")

    def unit_set(self, unit_id: str, **fields) -> None:
        with self.lock():
            rows = self._rows("units.tsv")
            row = next((r for r in rows if r["id"] == unit_id), None)
            if row is None:
                rows.append({h: "" for h in UNIT_HEADER} | {"id": unit_id} | fields)
            else:
                row.update(fields)
            self._save("units.tsv", UNIT_HEADER, rows)

    def units(self) -> list[dict]:
        return self._rows("units.tsv")

    def ledger_record(self, pr: str, sha: str, verdict: str, evidence: str, verifier: str) -> None:
        if verdict not in VERDICTS:
            raise StoreError(f"verdict must be one of: {', '.join(VERDICTS)}")
        with self.lock():
            rows = [r for r in self._rows("ledger.tsv") if not (r["pr"] == pr and r["sha"] == sha)]
            rows.append({"pr": pr, "sha": sha, "verdict": verdict, "evidence": evidence, "verifier": verifier,
                         "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            self._save("ledger.tsv", LEDGER_HEADER, rows)

    def ledger_check(self, pr: str, head_sha: str) -> str:
        """The verdict for this exact head, or 'unverified'. An older head's row never answers."""
        row = next((r for r in self._rows("ledger.tsv") if r["pr"] == pr and r["sha"] == head_sha), None)
        return row["verdict"] if row else "unverified"

    def inbox_push(self, agent: str, unit: str, status: str, report: str = "") -> str:
        name = f"{time.time_ns()}-{uuid.uuid4().hex[:6]}.json"
        atomic_write(self._path(os.path.join("inbox", name)),
                     json.dumps({"agent": agent, "unit": unit, "status": status, "report": report}))
        return name

    def inbox_drain(self) -> list[dict]:
        with self.lock():
            names = sorted(os.listdir(self._path("inbox")))
            pointers = [json.load(open(self._path(os.path.join("inbox", n)))) for n in names]
            for n in names:
                os.unlink(self._path(os.path.join("inbox", n)))
        return pointers

    def status(self) -> dict:
        units = self.units()
        counts: dict[str, int] = {}
        for unit in units:
            counts[unit["state"]] = counts.get(unit["state"], 0) + 1
        verified = [u["id"] for u in units if u["pr"] and self.ledger_check(u["pr"], u["sha"]) in PASSING]
        report = {"counts": counts, "verified": verified, "units": len(units)}
        atomic_write(self._path("status.md"), "# status (derived from units.tsv and ledger.tsv)\n\n" + json.dumps(report, indent=2) + "\n")
        return report
