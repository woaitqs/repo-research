"""Capabilities: how SandboxAgent turns a workspace into tools, prompt and sampling params.

Upstream: `Capability` (`src/agents/sandbox/capabilities/capability.py`) has five hooks:
`tools()`, `instructions(manifest)`, `sampling_params(params)`, `process_context(items)`,
`process_manifest(manifest)`. `prepare_sandbox_agent`
(`src/agents/sandbox/runtime_agent_preparation.py:96`) clones the public agent into an
*execution agent* with capability tools appended, instructions assembled in a fixed order,
and capability sampling params merged into `model_settings.extra_args`. The run loop calls
`SandboxRuntime.prepare_agent` before every turn (`src/agents/run.py:1079`), but
results and hooks keep reporting the public agent (`AgentBindings`).

`Workspace` stands in for `BaseSandboxSession`. Like `UnixLocalSandboxClient` on Linux it is
a temp directory plus subprocess, with no OS-level isolation.
"""

from __future__ import annotations

import asyncio
import copy
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent import Agent
from .items import Item
from .tool import FunctionTool, function_tool

BASE_PROMPT = "You are operating inside a workspace. Use the tools to inspect files before answering."


class Workspace:
    """A temp-dir workspace with `exec`, `read`, `write` (no isolation)."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="miniagents-ws-"))
        for rel, text in (files or {}).items():
            self.write(rel, text)

    def path(self, rel: str) -> Path:
        target = (self.root / rel).resolve()
        if self.root.resolve() not in (target, *target.parents):
            raise ValueError(f"path escapes workspace: {rel}")
        return target

    def write(self, rel: str, text: str) -> None:
        target = self.path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read(self, rel: str) -> str | None:
        target = self.path(rel)
        return target.read_text(encoding="utf-8") if target.exists() else None

    async def exec(self, cmd: str, timeout: float = 10.0) -> tuple[int, str]:
        def run() -> tuple[int, str]:
            proc = subprocess.run(
                cmd, shell=True, cwd=self.root, capture_output=True, text=True, timeout=timeout
            )
            return proc.returncode, proc.stdout + proc.stderr

        return await asyncio.to_thread(run)

    def tree(self) -> str:
        files = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return "\n".join(f"- {f}" for f in files)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class Capability:
    type = "capability"

    def __init__(self) -> None:
        self.workspace: Workspace | None = None

    def clone(self) -> Capability:
        # Per-run copy: bound state must not leak across runs (upstream `Capability.clone`).
        return copy.copy(self)

    def bind(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def process_manifest(self, files: dict[str, str]) -> dict[str, str]:
        return files

    def tools(self) -> list[FunctionTool]:
        return []

    def instructions(self) -> str | None:
        return None

    def sampling_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def process_context(self, items: list[Item]) -> list[Item]:
        return items


class Shell(Capability):
    type = "shell"

    def __init__(self, max_output_chars: int = 4000) -> None:
        super().__init__()
        self.max_output_chars = max_output_chars

    def tools(self) -> list[FunctionTool]:
        ws = self.workspace
        assert ws is not None, "Shell must be bound to a workspace"
        limit = self.max_output_chars

        @function_tool
        async def exec_command(cmd: str) -> str:
            """Run a shell command in the workspace and return its exit code and output."""
            code, out = await ws.exec(cmd)
            if len(out) > limit:
                out = out[:limit] + f"\n...[truncated {len(out) - limit} chars]"
            return f"exit_code={code}\n{out}"

        return [exec_command]

    def instructions(self) -> str:
        return "Use `exec_command` for shell commands. Prefer `grep -rn` to find text."


class Skills(Capability):
    """Index in the prompt, body on disk: progressive disclosure (upstream `Skills`, skills.py:621)."""

    type = "skills"

    def __init__(self, skills: dict[str, tuple[str, str]], path: str = ".agents") -> None:
        super().__init__()
        self.skills = skills  # name -> (description, SKILL.md body)
        self.path = path

    def process_manifest(self, files: dict[str, str]) -> dict[str, str]:
        files = dict(files)
        for name, (_, body) in self.skills.items():
            files[f"{self.path}/{name}/SKILL.md"] = body
        return files

    def instructions(self) -> str:
        lines = ["## Skills", "Open a skill's SKILL.md only when the task matches it."]
        for name, (description, _) in self.skills.items():
            lines.append(f"- {name}: {description} (file: {self.path}/{name}/SKILL.md)")
        return "\n".join(lines)


class Memory(Capability):
    """Inject `memories/memory_summary.md` into the prompt (upstream `Memory.instructions`)."""

    type = "memory"
    MAX_CHARS = 4000

    def instructions(self) -> str | None:
        assert self.workspace is not None
        summary = self.workspace.read("memories/memory_summary.md")
        if not summary or not summary.strip():
            return None
        return (
            "## Memory\nMemory is guidance, not truth: current evidence wins.\n"
            "===== MEMORY_SUMMARY BEGINS =====\n"
            f"{summary.strip()[: self.MAX_CHARS]}\n"
            "===== MEMORY_SUMMARY ENDS ====="
        )


class Compaction(Capability):
    """Ask the server to compact, and drop history older than the newest compaction item."""

    type = "compaction"

    def __init__(self, threshold: int = 240_000) -> None:
        super().__init__()
        self.threshold = threshold

    def sampling_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"context_management": [{"type": "compaction", "compact_threshold": self.threshold}]}

    def process_context(self, items: list[Item]) -> list[Item]:
        for index in range(len(items) - 1, -1, -1):
            if items[index].get("type") == "compaction":
                return items[index:]
        return items


@dataclass
class CapableAgent(Agent):
    """The `SandboxAgent` analogue: an Agent plus capabilities and a default manifest."""

    capabilities: list[Capability] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)


@dataclass
class PreparedAgent:
    public: Agent
    execution: Agent
    capabilities: list[Capability]

    def process_input(self, original_input: str | list[Item]) -> str | list[Item]:
        if isinstance(original_input, str):
            return original_input
        items = list(original_input)
        for capability in self.capabilities:
            items = capability.process_context(items)
        return items


class CapabilityRuntime:
    """Owns the workspace for one run and prepares an execution agent per (agent, run)."""

    def __init__(self, workspace: Workspace | None) -> None:
        self.workspace = workspace
        self._cache: dict[int, PreparedAgent] = {}
        self._materialized = False

    def prepare(self, agent: Agent) -> PreparedAgent:
        if not isinstance(agent, CapableAgent):
            return PreparedAgent(agent, agent, [])
        if self.workspace is None:
            raise ValueError("CapableAgent requires RunConfig(workspace=...)")
        cached = self._cache.get(id(agent))
        if cached is not None:
            return cached
        capabilities = [c.clone() for c in agent.capabilities]
        files = dict(agent.files)
        for capability in capabilities:
            files = capability.process_manifest(files)
        if not self._materialized:
            for rel, text in files.items():
                if self.workspace.read(rel) is None:
                    self.workspace.write(rel, text)
            self._materialized = True
        for capability in capabilities:
            capability.bind(self.workspace)

        settings = dict(agent.model_settings)
        for capability in capabilities:
            settings.update(capability.sampling_params(settings))
        execution = agent.clone(
            instructions=_build_instructions(agent, capabilities, self.workspace),
            tools=[*agent.tools, *(t for c in capabilities for t in c.tools())],
            model_settings=settings,
        )
        prepared = PreparedAgent(agent, execution, capabilities)
        self._cache[id(agent)] = prepared
        return prepared


def _build_instructions(agent: Agent, capabilities: list[Capability], ws: Workspace):
    """Order: base prompt, agent instructions, capability fragments, filesystem tree.

    Same order as `build_sandbox_instructions` (runtime_agent_preparation.py:171).
    """

    async def instructions(ctx: Any, current: Agent) -> str:
        parts = [BASE_PROMPT]
        own = await agent.get_system_prompt(ctx)  # Resolved against the *public* agent.
        if own:
            parts.append(f"# Agent instructions\n\n{own}")
        fragments = [f for f in (c.instructions() for c in capabilities) if f]
        if fragments:
            parts.append("# Capability instructions\n\n" + "\n\n".join(fragments))
        parts.append(f"# Filesystem\n\n{ws.tree()}")
        return "\n\n".join(parts)

    return instructions
