"""Workspace: the execution boundary between reasoning and side effects.

Mirrors openhands-sdk/openhands/sdk/workspace/ (BaseWorkspace / LocalWorkspace;
RemoteWorkspace and DockerWorkspace implement the same surface over HTTP to an
agent-server). The agent never touches the OS directly: tools call the
workspace, so swapping LocalWorkspace for a remote/container one changes where
commands run without changing the agent loop.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    output: str
    exit_code: int
    timed_out: bool = False


class Workspace(Protocol):
    working_dir: Path

    def execute_command(self, command: str, timeout: float = 30.0) -> CommandResult: ...

    def read_file(self, path: str) -> str: ...

    def write_file(self, path: str, content: str) -> None: ...


class LocalWorkspace:
    def __init__(self, working_dir: str | Path) -> None:
        self.working_dir = Path(working_dir).resolve()
        self.working_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        target = (self.working_dir / path).resolve()
        if self.working_dir not in (target, *target.parents):
            raise ValueError(f"path escapes workspace: {path}")
        return target

    def execute_command(self, command: str, timeout: float = 30.0) -> CommandResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return CommandResult(output=partial, exit_code=-1, timed_out=True)
        return CommandResult(output=proc.stdout + proc.stderr, exit_code=proc.returncode)

    def read_file(self, path: str) -> str:
        return self._resolve(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
