"""One typed source for model families, providers and role defaults.

Mirrors `runner/registry/` in open-pstack: family entries are data, each provider is an adapter
object, and a lane is built only by `resolve_lane`, which refuses everything the runner must not
launch (a same-provider lane, an unnormalized version pin, an effort the family does not list,
an access mode the provider cannot bound).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters import Adapter

EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra", "default")
ACCESS_MODES = ("read-only", "isolated-write")
UP_TO_MAX = ("low", "medium", "high", "xhigh", "max")

PARENTS = {
    "claude": {"native": "Agent", "identity": ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID")},
    "codex": {"native": "spawn_agent", "identity": ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_SANDBOX",
                                                    "CODEX_SANDBOX_NETWORK_DISABLED")},
}


class UsageError(ValueError):
    """A lane request the runner refuses before it reserves or launches anything."""


@dataclass(frozen=True)
class Family:
    name: str
    provider: str
    model: str
    efforts: tuple[str, ...]
    default_effort: str
    rolling_prefix: str | None = None
    predecessors: tuple[tuple[str, tuple[str, ...]], ...] = ()
    model_ids: dict[str, str] = field(default_factory=dict)

    def offered(self) -> tuple[str, ...]:
        return tuple(e for e in EFFORTS if e in self.model_ids) if self.model_ids else self.efforts


@dataclass(frozen=True)
class Access:
    mode: str
    sandbox: str = "bounded"

    @property
    def kind(self) -> str:
        return "isolated-write-full-access" if self.mode == "isolated-write" and self.sandbox == "full-access" else self.mode


@dataclass(frozen=True)
class Lane:
    parent: str
    adapter: "Adapter"
    family: Family | None
    model: str
    cli_model: str
    effort: str
    access: Access
    prompt_path: str
    cwd: str
    output_path: str
    receipt_path: str
    timeout_s: float | None = None


FAMILIES = (
    Family("opus", "claude", "opus", UP_TO_MAX, "max", rolling_prefix="claude-opus-"),
    Family("sonnet", "claude", "sonnet", UP_TO_MAX, "max", rolling_prefix="claude-sonnet-"),
    Family("haiku-5-5", "claude", "claude-haiku-5-5", UP_TO_MAX, "max"),
    Family("sol", "codex", "gpt-6.1-sol", UP_TO_MAX + ("ultra",), "max",
           predecessors=(("gpt-5.6-sol", UP_TO_MAX + ("ultra",)),)),
    Family("luna", "codex", "gpt-6-luna", UP_TO_MAX, "max"),
    Family("grok", "grok", "grok-4.7", UP_TO_MAX, "xhigh", predecessors=(("grok-4.6", UP_TO_MAX),)),
    Family("swe-2", "devin", "swe-2", (), "max", model_ids={"medium": "swe-2-medium", "high": "swe-2-high", "max": "swe-2-max"}),
)

PANEL = ("opus", "sol", "grok")
ROLE_DEFAULTS = (
    ("feature, refactoring", ("grok",)),
    ("bug-fix", ("sol",)),
    ("judgment and prose", ("opus",)),
    ("why investigators, synthesizer", ("inherit-parent",)),
    ("arena runners", PANEL),
    ("interrogate reviewers", PANEL),
    ("swarm workers", ("grok",)),
)
WRITER_ROLES = ("feature, refactoring", "bug-fix", "arena runners", "swarm workers")


def family_named(name: str) -> Family:
    return next(f for f in FAMILIES if f.name == name)


def descriptor(family: Family, effort: str | None = None) -> str:
    return f"{family.provider}:{family.model}@{effort or family.default_effort}"


def resolve_model(provider: str, model: str) -> tuple[str, Family | None, tuple[str, ...] | None]:
    """Match in the catalog's order: current row, kept predecessor, rolling-alias revision, else unregistered."""
    candidates = [f for f in FAMILIES if f.provider == provider]
    for family in candidates:
        if family.model == model:
            return "current", family, None
    for family in candidates:
        for kept, efforts in family.predecessors:
            if kept == model:
                return "kept", family, efforts
    for family in candidates:
        prefix = family.rolling_prefix
        if prefix and model.startswith(prefix) and re.fullmatch(r"[0-9]+(?:-[0-9]+)*", model[len(prefix):]):
            return "stale-revision", family, None
    return "unregistered", None, None


def resolve_lane(*, parent: str, provider: str, model: str, effort: str, mode: str, prompt_path: str, cwd: str,
                 output_path: str, receipt_path: str, writer_sandbox: str = "bounded", timeout_s: float | None = None,
                 adapters: dict | None = None) -> Lane:
    from .adapters import ADAPTERS

    adapters = adapters or ADAPTERS
    if mode not in ACCESS_MODES:
        raise UsageError(f"mode must be one of: {', '.join(ACCESS_MODES)}")
    if writer_sandbox == "full-access" and mode != "isolated-write":
        raise UsageError("writer sandbox full-access requires mode isolated-write")
    if parent not in PARENTS:
        raise UsageError(f"parent must be one of: {', '.join(PARENTS)}")
    if provider not in adapters:
        raise UsageError(f"provider must be one of: {', '.join(adapters)}")
    if effort not in EFFORTS:
        raise UsageError(f"effort must be one of: {', '.join(EFFORTS)}")
    if provider == parent:
        raise UsageError(f"provider {provider} is native to parent {parent}; use the parent subagent primitive")
    adapter = adapters[provider]
    kind, family, kept_efforts = resolve_model(provider, model)
    if kind == "stale-revision":
        raise UsageError(f"model {model} is a version pin; normalize it to {family.model} before invoking the runner")
    if kind == "kept":
        offered = kept_efforts
    elif family is None:
        offered = ("default",) if adapter.effort_in == "model-id" else EFFORTS
    else:
        offered = family.offered()
    if effort not in offered:
        raise UsageError(f"effort {effort} is not offered for {provider}:{model}; choose one of: {', '.join(offered)}")
    access = Access(mode, writer_sandbox)
    if access.kind not in adapter.supports:
        raise UsageError(f"provider {provider} does not support {access.kind}; it supports only: {', '.join(adapter.supports)}")
    cli_model = family.model_ids.get(effort, model) if family is not None else model
    return Lane(parent, adapter, family if kind != "unregistered" else None, model, cli_model, effort, access,
                prompt_path, cwd, output_path, receipt_path, timeout_s)
