"""spawn_agent asks for a sibling thread. It does not run the child."""

from __future__ import annotations

from typing import Any

from minispace.tools import Tool

READ_TOOLS = {
    "read",
    "ls",
    "tree",
    "grep",
    "glob",
    "skill",
    "web_search",
    "web_fetch",
    "calculator",
    "date_difference",
    "sleep",
}
ROLES = ("general-purpose", "researcher", "code-reviewer")


def filter_tools(tools: list[Tool], role: str) -> list[Tool]:
    kept: list[Tool] = []
    for tool in tools:
        if tool.kind != "builtin":
            kept.append(tool)
            continue
        if tool.name == "spawn_agent":
            continue
        if role == "general-purpose":
            kept.append(tool)
        elif tool.name in READ_TOOLS or (
            role == "code-reviewer" and tool.name in ("bash", "exec_code")
        ):
            kept.append(tool)
    return kept


def build_subagent_thread(
    parent: dict[str, Any],
    *,
    parent_name: str,
    task_name: str,
    prompt: str,
    role: str = "general-purpose",
) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError(role)
    messages: list[dict[str, Any]] = []
    parent_messages = parent.get("messages") or []
    if parent_messages and parent_messages[0].get("meta"):
        messages.append(dict(parent_messages[0]))
    messages.append(
        {
            "role": "user",
            "text": (
                f"Parent task: {parent_name}\nTask: {task_name}\nRole: {role}\n\n{prompt}"
            ),
        }
    )
    return {
        "system": parent.get("system", ""),
        "messages": messages,  # CHILD_ISOLATED
        "values": dict(parent.get("values") or {}),
        "snapshot": {},
        "tools": filter_tools(list(parent.get("tools") or []), role),
        "executed": False,
    }
