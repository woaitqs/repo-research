"""Pluggable backends: *where* files live and *whether* a shell exists.

Mirrors deepagents/backends/:

- `BackendProtocol`   file ops (ls/read/write/edit/grep); `execute` is optional
- `StateBackend`      files live in agent state (`state["files"]`), written through the
                      graph runtime rather than returned by tools
                      (upstream: LangGraph `CONFIG_KEY_READ` / `CONFIG_KEY_SEND`)
- `DirBackend`        files on disk under a virtual root (upstream `FilesystemBackend`)
- `ShellBackend`      `DirBackend` + local `execute` — NO isolation (upstream `LocalShellBackend`)
- `CompositeBackend`  longest-prefix routing across backends; `execute` always goes to
                      the default backend
"""

from __future__ import annotations

import contextvars
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass
class RunContext:
    """Per-node view of graph state, set by the agent loop around every node."""

    state: dict[str, Any]
    pending_files: dict[str, str | None] = field(default_factory=dict)

    def read_files(self) -> dict[str, str]:
        files = dict(self.state.get("files", {}))
        for path, content in self.pending_files.items():  # read-your-writes within a node
            if content is None:
                files.pop(path, None)
            else:
                files[path] = content
        return files


run_context: contextvars.ContextVar[RunContext] = contextvars.ContextVar("minideep_run_context")


def _norm(path: str) -> str:
    if not path.startswith("/"):
        msg = f"path must be absolute: {path!r}"
        raise ValueError(msg)
    if ".." in PurePosixPath(path).parts:
        msg = f"path traversal is not allowed: {path!r}"
        raise ValueError(msg)
    return str(PurePosixPath(path))


class BackendProtocol:
    def ls(self, path: str) -> list[str]:
        raise NotImplementedError

    def read(self, path: str) -> str:
        raise NotImplementedError

    def write(self, path: str, content: str) -> None:
        raise NotImplementedError

    def edit(self, path: str, old: str, new: str) -> None:
        content = self.read(path)
        count = content.count(old)
        if count != 1:
            msg = f"old_string must appear exactly once in {path} (found {count})"
            raise ValueError(msg)
        self.write(path, content.replace(old, new))

    def grep(self, pattern: str, path: str = "/") -> list[tuple[str, int, str]]:
        hits = []
        for file_path in self.ls(path):
            for lineno, line in enumerate(self.read(file_path).splitlines(), 1):
                if pattern in line:  # literal, not regex (same as upstream `grep`)
                    hits.append((file_path, lineno, line))
        return hits


class StateBackend(BackendProtocol):
    """Files stored in `state["files"]`; thread-scoped and checkpointed with the state."""

    def _ctx(self) -> RunContext:
        try:
            return run_context.get()
        except LookupError:
            msg = "StateBackend must be used inside an agent run (no active RunContext)"
            raise RuntimeError(msg) from None

    def ls(self, path: str) -> list[str]:
        prefix = _norm(path).rstrip("/") + "/"
        return sorted(p for p in self._ctx().read_files() if p.startswith(prefix))

    def read(self, path: str) -> str:
        files = self._ctx().read_files()
        if _norm(path) not in files:
            msg = f"file not found: {path}"
            raise FileNotFoundError(msg)
        return files[_norm(path)]

    def write(self, path: str, content: str) -> None:
        self._ctx().pending_files[_norm(path)] = content


class DirBackend(BackendProtocol):
    """Files on disk; virtual paths are resolved under `root` and cannot escape it."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _real(self, path: str) -> Path:
        real = (self.root / _norm(path).lstrip("/")).resolve()
        if real != self.root and self.root not in real.parents:
            msg = f"path escapes backend root: {path!r}"
            raise ValueError(msg)
        return real

    def ls(self, path: str) -> list[str]:
        base = self._real(path)
        if not base.exists():
            return []
        return sorted("/" + str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

    def read(self, path: str) -> str:
        return self._real(path).read_text()

    def write(self, path: str, content: str) -> None:
        real = self._real(path)
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(content)


class ShellBackend(DirBackend):
    """DirBackend + `execute`. Commands run on the host: path checks are NOT a sandbox."""

    def __init__(self, root: str | Path, timeout: int = 30) -> None:
        super().__init__(root)
        self.timeout = timeout

    def execute(self, command: str) -> tuple[str, int]:
        proc = subprocess.run(  # noqa: S602 - deliberate: this is the "local shell" backend
            command, shell=True, cwd=self.root, capture_output=True, text=True, timeout=self.timeout, check=False
        )
        return proc.stdout + proc.stderr, proc.returncode


class CompositeBackend(BackendProtocol):
    """Route by longest path prefix; unmatched paths go to `default`."""

    def __init__(self, default: BackendProtocol, routes: dict[str, BackendProtocol], artifacts_root: str = "/") -> None:
        self.default = default
        self.routes = sorted(routes.items(), key=lambda kv: len(kv[0]), reverse=True)
        self.artifacts_root = artifacts_root

    def _route(self, path: str) -> tuple[BackendProtocol, str, str]:
        for prefix, backend in self.routes:
            if path.startswith(prefix):
                return backend, "/" + path[len(prefix) :], prefix
        return self.default, path, ""

    def ls(self, path: str) -> list[str]:
        backend, inner, prefix = self._route(path)
        found = [prefix.rstrip("/") + p if prefix else p for p in backend.ls(inner)]
        if not prefix and path == "/":  # root listing also shows routed subtrees
            for route_prefix, route_backend in self.routes:
                found += [route_prefix.rstrip("/") + p for p in route_backend.ls("/")]
        return sorted(found)

    def read(self, path: str) -> str:
        backend, inner, _ = self._route(path)
        return backend.read(inner)

    def write(self, path: str, content: str) -> None:
        backend, inner, _ = self._route(path)
        backend.write(inner, content)

    def execute(self, command: str) -> tuple[str, int]:
        return self.default.execute(command)  # type: ignore[attr-defined]


def supports_execution(backend: BackendProtocol) -> bool:
    """Capability probe used to hide `execute` from the model when no shell exists."""
    if isinstance(backend, CompositeBackend):
        return supports_execution(backend.default)
    return callable(getattr(backend, "execute", None))


def artifacts_root(backend: BackendProtocol) -> str:
    return backend.artifacts_root.rstrip("/") if isinstance(backend, CompositeBackend) else ""
