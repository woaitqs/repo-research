"""Permission classification, evaluated by the client *before* execution.

Mirrors `src/permissions/checker.ts:238-801` (order condensed) and
`src/cli/helpers/approval-classification.ts:124-230`:

1. cross-agent memory guard - no mode can bypass it;
2. deny rules;
3. **mode override**: `unrestricted` (the upstream default, mode.ts:10) allows
   everything, `acceptEdits` allows Write/Edit;
4. read-only tools inside the working directory are auto-allowed
   (skipped in `strict` mode);
5. allow rules, then ask rules, then the default: ask.

Rules look like `Read(src/**)` or `Bash(git status:*)`; a Bash prefix rule
matches the command's first segment, as upstream does.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Any

READ_ONLY = {"Read", "Grep", "Glob"}
EDIT_TOOLS = {"Write", "Edit"}


@dataclass
class PermissionPolicy:
    mode: str = "unrestricted"
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)
    cwd: str = "."
    other_agents_memory_root: str | None = None
    own_memory_dir: str | None = None

    def check(self, tool: str, args: dict[str, Any]) -> tuple[str, str]:
        path = args.get("file_path")
        if path and self.other_agents_memory_root:
            real = os.path.realpath(os.path.join(self.cwd, path))
            root = os.path.realpath(self.other_agents_memory_root)
            own = os.path.realpath(self.own_memory_dir) if self.own_memory_dir else None
            if real.startswith(root + os.sep) and not (own and real.startswith(own + os.sep)):
                return "deny", "cross-agent memory guard"
        if self._match(self.deny, tool, args):
            return "deny", "deny rule"
        if self.mode == "unrestricted":
            return "allow", "mode: unrestricted"
        if self.mode == "acceptEdits" and tool in EDIT_TOOLS:
            return "allow", "mode: acceptEdits"
        if self.mode != "strict" and tool in READ_ONLY and self._inside_cwd(path):
            return "allow", "read-only in working directory"
        if self._match(self.allow, tool, args):
            return "allow", "allow rule"
        if self._match(self.ask, tool, args):
            return "ask", "ask rule"
        return "ask", "default"

    def _inside_cwd(self, path: str | None) -> bool:
        if not path:
            return True
        real = os.path.realpath(os.path.join(self.cwd, path))
        return real == os.path.realpath(self.cwd) or real.startswith(os.path.realpath(self.cwd) + os.sep)

    @staticmethod
    def _match(rules: list[str], tool: str, args: dict[str, Any]) -> bool:
        for rule in rules:
            name, _, pattern = rule.partition("(")
            if name != tool:
                continue
            pattern = pattern.rstrip(")")
            if not pattern:
                return True
            if tool == "Bash":
                first = args.get("command", "").split("&&")[0].split("|")[0].strip()
                if pattern.endswith(":*") and first.startswith(pattern[:-2]) or first == pattern:
                    return True
            elif fnmatch.fnmatch(args.get("file_path", ""), pattern):
                return True
        return False


def classify(calls: list[dict[str, Any]], policy: PermissionPolicy) -> dict[str, list]:
    """Split calls into auto-allowed, auto-denied and needs-user-input."""
    out: dict[str, list] = {"allowed": [], "denied": [], "ask": []}
    for call in calls:
        decision, reason = policy.check(call["name"], call["arguments"])
        bucket = {"allow": "allowed", "deny": "denied", "ask": "ask"}[decision]
        out[bucket].append((call, reason))
    return out
