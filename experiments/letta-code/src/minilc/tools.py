"""Client-side tools: schemas go to the backend, execution stays here.

Mirrors `src/tools/` + `src/agent/approval-execution.ts`:

* a tool is `{name, description, parameters, impl}`; the registry serialises
  schemas as `client_tools` on *every* request (`agent/message.ts:310-337`);
* `execute()` is the manager pipeline (`tools/manager.ts:1966-2433`): unknown
  tool -> error text, PreToolUse hooks (block or rewrite input), run, scrub
  secrets, clamp to 32k chars with the full output written to an overflow file;
  errors never propagate - they become `status="error"` results;
* `execute_batch()` runs read-only tools in parallel, `Edit`/`Write` under a
  per-file lock and everything else under one global lock, preserving order.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

TOOL_RETURN_MAX_CHARS = 32_000           # truncation.ts:37
PARALLEL_SAFE = {"Read", "Grep", "Glob", "Agent"}  # approval-execution.ts:45-64 (subset)
FILE_PATH_TOOLS = {"Edit", "Write"}      # approval-execution.ts:70


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    impl: Callable[[dict[str, Any], "ToolContext"], Any]


@dataclass
class ToolContext:
    cwd: str
    overflow_dir: str
    secrets: dict[str, str] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


Hook = Callable[[str, dict[str, Any]], dict[str, Any] | None]  # -> {"block": reason} | {"updated_input": {...}}


class ToolRegistry:
    def __init__(self, tools: list[Tool], pre_tool_hooks: list[Hook] | None = None):
        self.tools = {t.name: t for t in tools}
        self.pre_tool_hooks = pre_tool_hooks or []

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in self.tools.values()]

    def without(self, *names: str) -> "ToolRegistry":
        return ToolRegistry([t for n, t in self.tools.items() if n not in names], self.pre_tool_hooks)

    # -- one call -----------------------------------------------------------
    def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            return {"tool_return": f"Tool not found: {name}. Available tools: {', '.join(self.tools)}", "status": "error"}
        for hook in self.pre_tool_hooks:
            verdict = hook(name, args) or {}
            if "block" in verdict:
                return {"tool_return": f"Error: Tool execution blocked by hook. {verdict['block']}", "status": "error"}
            args = {**args, **verdict.get("updated_input", {})}
        try:
            raw = tool.impl(args, ctx)
            status = "error" if isinstance(raw, dict) and raw.get("status") == "error" else "success"
            text = raw.get("content", "") if isinstance(raw, dict) else str(raw)
        except Exception as err:  # noqa: BLE001 - errors are observations, never raised
            status, text = "error", f"Error executing tool: {err}"
        return {"tool_return": clamp(scrub(text, ctx.secrets), name, ctx), "status": status}

    # -- a batch of approved calls -------------------------------------------
    def execute_batch(self, calls: list[dict[str, Any]], ctx: ToolContext) -> list[dict[str, Any]]:
        locks: dict[str, threading.Lock] = {}
        guard = threading.Lock()

        def resource_key(call: dict[str, Any]) -> str | None:
            if call["name"] in PARALLEL_SAFE:
                return None
            if call["name"] in FILE_PATH_TOOLS and isinstance(call["arguments"].get("file_path"), str):
                return os.path.abspath(os.path.join(ctx.cwd, call["arguments"]["file_path"]))
            return "__global__"

        def run(call: dict[str, Any]) -> dict[str, Any]:
            key = resource_key(call)
            if key is None:
                return self.execute(call["name"], call["arguments"], ctx)
            with guard:
                lock = locks.setdefault(key, threading.Lock())
            with lock:
                return self.execute(call["name"], call["arguments"], ctx)

        with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
            return list(pool.map(run, calls))  # map keeps the original order


def scrub(text: str, secrets: dict[str, str]) -> str:
    """secret-substitution.ts:251-264: replace values (longest first), skip short ones."""
    for name, value in sorted(secrets.items(), key=lambda kv: -len(kv[1])):
        if len(value) >= 8:
            text = text.replace(value, f"{name}=<REDACTED>")
    return text


def clamp(text: str, tool_name: str, ctx: ToolContext) -> str:
    """truncation.ts:85-134: keep head and tail, write the full output to a file."""
    if len(text) <= TOOL_RETURN_MAX_CHARS:
        return text
    os.makedirs(ctx.overflow_dir, exist_ok=True)
    path = os.path.join(ctx.overflow_dir, f"{tool_name}-{uuid.uuid4().hex[:8]}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    half = TOOL_RETURN_MAX_CHARS // 2
    omitted = len(text) - 2 * half
    return (f"{text[:half]}\n... [Output truncated: {omitted} characters omitted] ...\n{text[-half:]}"
            f"\n[Full output written to: {path}]")


# -- a few concrete tools ------------------------------------------------------
def _path(ctx: ToolContext, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(ctx.cwd, p)


def _read(args: dict[str, Any], ctx: ToolContext) -> str:
    with open(_path(ctx, args["file_path"]), encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = int(args.get("offset", 0))
    return "\n".join(f"{i + 1}\t{line}" for i, line in enumerate(lines[start:start + 2000], start))


def _write(args: dict[str, Any], ctx: ToolContext) -> str:
    path = _path(ctx, args["file_path"])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(args["content"])
    return f"Successfully wrote {len(args['content'])} characters to {path}"


def _edit(args: dict[str, Any], ctx: ToolContext) -> Any:
    path = _path(ctx, args["file_path"])
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.count(args["old_string"]) != 1:
        return {"status": "error", "content": "old_string must occur exactly once"}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(args["old_string"], args["new_string"]))
    return f"Successfully replaced 1 occurrence in {path}"


def _bash(args: dict[str, Any], ctx: ToolContext) -> Any:
    import subprocess
    env = {**os.environ, **ctx.secrets, **ctx.extras.get("env", {})}
    proc = subprocess.run(args["command"], shell=True, cwd=ctx.cwd, env=env, capture_output=True,
                          text=True, timeout=int(args.get("timeout_ms", 120_000)) / 1000)
    out = (proc.stdout + proc.stderr).rstrip()
    return {"status": "error", "content": f"Exit code {proc.returncode}\n{out}"} if proc.returncode else out


def default_tools() -> list[Tool]:
    obj = lambda **props: {"type": "object", "properties": props, "required": list(props)}  # noqa: E731
    s = {"type": "string"}
    return [
        Tool("Read", "Read a file (line-numbered).", obj(file_path=s), _read),
        Tool("Write", "Write a file.", obj(file_path=s, content=s), _write),
        Tool("Edit", "Replace one exact occurrence of old_string.", obj(file_path=s, old_string=s, new_string=s), _edit),
        Tool("Bash", "Run a shell command in the working directory.", obj(command=s), _bash),
    ]
