"""Tools: serializable spec -> registry factory -> executable definition.

Mirrors openhands-sdk/openhands/sdk/tool/{spec.py,registry.py,tool.py}:
* `ToolSpec(name, params)` is what the persisted Agent stores (pure data).
* `register_tool(name, factory)` maps a name to a factory; `resolve_tool`
  builds concrete `ToolDefinition`s *per conversation* (the factory receives
  the conversation state + workspace, so tools can bind to the right
  sandbox).
* A `ToolDefinition` carries the LLM-facing JSON schema and the executor.

Large outputs are truncated before they reach the model, and the full text
is offloaded to `<conversation>/observations/` (the SDK's TerminalTool does
the same via `full_output_save_dir`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .conversation import Conversation

MAX_OBSERVATION_CHARS = 2_000  # the SDK uses 30_000 (LLM.max_message_chars)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False


Executor = Callable[[dict[str, Any], "Conversation"], ToolResult]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    executor: Executor
    read_only: bool = False

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Tiny stand-in for the SDK's Pydantic Action validation."""
        props = self.parameters.get("properties", {})
        missing = [k for k in self.parameters.get("required", []) if k not in arguments]
        if missing:
            raise ValueError(f"missing required parameters: {missing}")
        unknown = [k for k in arguments if k not in props]
        if unknown:
            raise ValueError(f"unknown parameters: {unknown}")
        for key, value in arguments.items():
            expected = props[key].get("type")
            if expected == "string" and not isinstance(value, str):
                raise ValueError(f"parameter {key!r} must be a string")
            if expected == "integer" and not isinstance(value, int):
                raise ValueError(f"parameter {key!r} must be an integer")
            if "enum" in props[key] and value not in props[key]["enum"]:
                raise ValueError(f"parameter {key!r} must be one of {props[key]['enum']}")
        return arguments


ToolFactory = Callable[[dict[str, Any]], list[ToolDefinition]]
_REGISTRY: dict[str, ToolFactory] = {}


def register_tool(name: str, factory: ToolFactory) -> None:
    _REGISTRY[name] = factory


def resolve_tool(spec: ToolSpec) -> list[ToolDefinition]:
    if spec.name not in _REGISTRY:
        raise KeyError(f"tool {spec.name!r} is not registered")
    return _REGISTRY[spec.name](spec.params)


# ---------------------------------------------------------------- output bounding


def bound_output(text: str, conversation: "Conversation", tool: str, limit: int) -> str:
    """Keep head + tail, cut the middle, offload the full text (like the SDK's
    `openhands.sdk.utils.truncate.maybe_truncate(..., save_dir=...)`)."""
    if len(text) <= limit:
        return text
    note = f"\n[... output truncated: {len(text)} chars"
    obs_dir = conversation.state.observations_dir
    if obs_dir is not None:
        obs_dir.mkdir(parents=True, exist_ok=True)
        path = obs_dir / f"{tool}_output_{uuid.uuid4().hex[:8]}.txt"
        path.write_text(text, encoding="utf-8")
        note += f"; full output saved to {path.as_posix()}"
    note += " ...]\n"
    budget = max(limit - len(note), 0)
    head = budget // 2 + budget % 2
    tail = budget - head
    return text[:head] + note + (text[-tail:] if tail else "")


# ---------------------------------------------------------------- built-in tools


def _finish(args: dict[str, Any], conversation: "Conversation") -> ToolResult:
    return ToolResult(content=args["message"])


def _think(args: dict[str, Any], conversation: "Conversation") -> ToolResult:
    return ToolResult(content="Your thought has been logged.")


def _terminal_factory(params: dict[str, Any]) -> list[ToolDefinition]:
    limit = int(params.get("max_output_chars", MAX_OBSERVATION_CHARS))
    timeout = float(params.get("timeout", 30.0))

    def run(args: dict[str, Any], conversation: "Conversation") -> ToolResult:
        result = conversation.workspace.execute_command(args["command"], timeout=timeout)
        body = bound_output(result.output, conversation, "terminal", limit)
        suffix = (
            f"\n[command timed out after {timeout}s]"
            if result.timed_out
            else f"\n[exit code {result.exit_code}]"
        )
        return ToolResult(content=body + suffix, is_error=result.exit_code != 0)

    return [
        ToolDefinition(
            name="terminal",
            description="Run a shell command inside the workspace.",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            executor=run,
        )
    ]


def _file_editor_factory(params: dict[str, Any]) -> list[ToolDefinition]:
    def run(args: dict[str, Any], conversation: "Conversation") -> ToolResult:
        ws = conversation.workspace
        command, path = args["command"], args["path"]
        if command == "view":
            return ToolResult(content=ws.read_file(path))
        if command == "create":
            if "file_text" not in args:
                raise ValueError("create requires file_text")
            ws.write_file(path, args["file_text"])
            return ToolResult(content=f"File created: {path}")
        if command == "str_replace":
            text = ws.read_file(path)
            old, new = args.get("old_str", ""), args.get("new_str", "")
            if text.count(old) != 1:
                raise ValueError(f"old_str must occur exactly once in {path}")
            ws.write_file(path, text.replace(old, new))
            return ToolResult(content=f"Edited {path}")
        raise ValueError(f"unknown command {command}")

    return [
        ToolDefinition(
            name="file_editor",
            description="View, create or edit files in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "enum": ["view", "create", "str_replace"]},
                    "path": {"type": "string"},
                    "file_text": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["command", "path"],
            },
            executor=run,
        )
    ]


FINISH_TOOL = ToolDefinition(
    name="finish",
    description="Signal that the task is complete and give the final message.",
    parameters={
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
    executor=_finish,
    read_only=True,
)
THINK_TOOL = ToolDefinition(
    name="think",
    description="Log a thought without side effects.",
    parameters={
        "type": "object",
        "properties": {"thought": {"type": "string"}},
        "required": ["thought"],
    },
    executor=_think,
    read_only=True,
)
BUILT_IN_TOOLS = (FINISH_TOOL, THINK_TOOL)

register_tool("terminal", _terminal_factory)
register_tool("file_editor", _file_editor_factory)

__all__ = [
    "BUILT_IN_TOOLS",
    "MAX_OBSERVATION_CHARS",
    "ToolDefinition",
    "ToolResult",
    "ToolSpec",
    "bound_output",
    "register_tool",
    "resolve_tool",
]
