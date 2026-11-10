"""Who is allowed to run a tool. Function stubs and terminate tools are human gates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str  # function | builtin | mcp | plugin | provider-hosted
    terminate: bool = False


# A small stand-in for the upstream denylist. A hit only forces a hand run.
_DANGEROUS = (
    re.compile(r"\brm\s+", re.I),
    re.compile(r"\bsudo\b", re.I),
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b", re.I),
)


def is_executable(tool: Tool) -> bool:
    """MCP and plugin tools have a backend. Built-ins do, unless they must stop."""
    if tool.kind in ("mcp", "plugin"):
        return True
    return (
        tool.kind == "builtin"
        and tool.name not in ("ask_user_question", "spawn_agent")
        and not tool.terminate
    )


def is_dangerous_bash(command: str) -> bool:
    return any(pattern.search(command) for pattern in _DANGEROUS)


def needs_human(tool: Tool, arguments: dict[str, Any]) -> bool:
    if not is_executable(tool):
        return True
    if tool.kind == "builtin" and tool.name == "bash":
        command = arguments.get("command")
        if isinstance(command, str) and is_dangerous_bash(command):
            return True
    return False
