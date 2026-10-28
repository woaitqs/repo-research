"""What a skill pack puts into the model's context, and when.

Mirrors the plugin's context design. At session start the SessionStart hook injects one short
mandate (only on startup, clear and compact), and the harness lists each user-invocable skill by
name and description. A skill's body enters context only when the skill is invoked. Principle
leaves are `user-invocable: false`: they are hidden from both lists and reached only by path from
poteto-mode's index, so the model reads one when a decision calls for it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

HOOK_EVENTS = ("startup", "clear", "compact")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: str
    user_invocable: bool
    model_invocable: bool


def _frontmatter(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        return {}, text
    meta = {}
    for line in match[1].splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"')
    return meta, match[2]


def load_tree(root: str) -> dict[str, Skill]:
    tree = {}
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, "SKILL.md")
        if not os.path.exists(path):
            continue
        meta, body = _frontmatter(open(path).read())
        tree[name] = Skill(meta.get("name", name), meta.get("description", ""), body, path,
                           meta.get("user-invocable") != "false", meta.get("disable-model-invocation") != "true")
    return tree


def session_start(mandate: str, event: str) -> str | None:
    return mandate if event in HOOK_EVENTS else None


def listed(tree: dict[str, Skill], namespace: str = "pstack") -> list[str]:
    return [f"{namespace}:{s.name}" for s in tree.values() if s.user_invocable]


def startup_context(tree: dict[str, Skill], mandate: str) -> str:
    lines = [f"- {name}: {tree[name.split(':', 1)[1]].description}" for name in listed(tree)]
    return mandate + "\n\nAvailable skills:\n" + "\n".join(lines)


def invoke(tree: dict[str, Skill], qualified: str) -> str:
    name = qualified.split(":", 1)[-1]
    skill = tree.get(name)
    if skill is None or not skill.user_invocable:
        raise KeyError(f"{qualified} is not an invocable skill")
    return skill.body


def principle_path(entry_skill_path: str, title: str) -> str:
    """poteto-mode's rule: the leaf is ../principle-<title in kebab case>/SKILL.md from its own file."""
    kebab = re.sub(r"[^a-z0-9-]", "", re.sub(r"\s+", "-", title.lower()))
    return os.path.normpath(os.path.join(os.path.dirname(entry_skill_path), "..", f"principle-{kebab}", "SKILL.md"))
