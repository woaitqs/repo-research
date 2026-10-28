"""Generate the model catalog and native agent files from the registry, and check for drift.

Mirrors `runner/registry/render.ts` and its tests. The parent model reads markdown, not code, so
the facts it needs are rendered from the one typed source. A committed file that differs from the
render is a test failure that names the write command. A text scan keeps descriptors and
versioned model names out of every hand-written file, so a model change edits no prose.
"""

from __future__ import annotations

import os
import re

from .adapters import ADAPTERS
from .registry import FAMILIES, PARENTS, ROLE_DEFAULTS, descriptor, family_named

CATALOG = "model-catalog.md"
WRITE_HINT = "Run `python3 -m minipstack.render --write <dir>`."


def render_catalog() -> str:
    rows = ["| Family | Provider | Model | Default effort | Selectable efforts |", "| --- | --- | --- | --- | --- |"]
    rows += [f"| {f.name} | {f.provider} | {f.model} | {f.default_effort} | {' '.join(f.offered())} |" for f in FAMILIES]
    routes = ["| Provider | " + " | ".join(f"{p} parent" for p in PARENTS) + " | Access modes |", "| --- |" + " --- |" * (len(PARENTS) + 1)]
    for adapter in ADAPTERS.values():
        cells = [f"native `{PARENTS[p]['native']}`" if adapter.id == p else "external runner" for p in PARENTS]
        routes.append(f"| {adapter.id} | {' | '.join(cells)} | {', '.join(adapter.supports)} |")
    sheet = [f"{role}: {', '.join(lane if lane in ('inherit-parent', 'auto') else descriptor(family_named(lane)) for lane in lanes)}"
             for role, lanes in ROLE_DEFAULTS]
    return "\n".join([f"<!-- Generated from registry.py; do not edit. {WRITE_HINT} -->", "", "# Model catalog", "",
                      "## Model matrix", "", *rows, "", "## Routes", "", *routes, "", "## Role defaults", "", "```text",
                      *sheet, "```", ""])


def render_agents() -> dict[str, str]:
    files = {}
    for family in (f for f in FAMILIES if f.provider == "claude"):
        for effort in family.offered():
            name = f"pstack-{family.name}-{effort}"
            files[f"agents/{name}.md"] = (
                f"---\nname: {name}\ndescription: Native Claude lane for pstack roles configured as "
                f"{family.provider}:{family.model}@{effort}.\nmodel: {family.model}\neffort: {effort}\n"
                f"disallowedTools: Agent, Task\n---\n\nExecute only the task and path scope the parent assigns. "
                f"Do not choose another model, spawn another agent, or start a pstack workflow.\n")
    return files


def generated() -> dict[str, str]:
    return {CATALOG: render_catalog(), **render_agents()}


def drift(root: str) -> list[str]:
    problems = []
    for path, content in generated().items():
        full = os.path.join(root, path)
        if not os.path.exists(full):
            problems.append(f"missing {path}; {WRITE_HINT}")
        elif open(full).read() != content:
            problems.append(f"{path} differs from the render; {WRITE_HINT}")
    agents_dir = os.path.join(root, "agents")
    expected = {os.path.basename(p) for p in render_agents()}
    if os.path.isdir(agents_dir):
        problems += [f"stray agent agents/{n}; {WRITE_HINT}" for n in sorted(os.listdir(agents_dir))
                     if n.startswith("pstack-") and n not in expected]
    return problems


def write(root: str) -> None:
    os.makedirs(os.path.join(root, "agents"), exist_ok=True)
    expected = {os.path.basename(p) for p in render_agents()}
    for name in os.listdir(os.path.join(root, "agents")):
        if name.startswith("pstack-") and name not in expected:
            os.unlink(os.path.join(root, "agents", name))
    for path, content in generated().items():
        with open(os.path.join(root, path), "w") as f:
            f.write(content)


def text_scan(root: str) -> list[str]:
    """Hand-written markdown names roles, never a descriptor or a versioned model."""
    providers = "|".join(ADAPTERS)
    patterns = [re.compile(rf"\b(?:{providers}):[A-Za-z0-9._/-]+@[a-z]+")]
    patterns += [re.compile(r"[-\s]".join(re.escape(part) for part in f.model.split("-")), re.I)
                 for f in FAMILIES if re.search(r"[0-9]", f.model)]
    skip = set(generated())
    hits = []
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.relpath(os.path.join(folder, name), root)
            if not name.endswith(".md") or path in skip:
                continue
            for number, line in enumerate(open(os.path.join(folder, name)), 1):
                if any(p.search(line) for p in patterns):
                    hits.append(f"{path}:{number}: {line.strip()}")
    return hits


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "--write":
        write(sys.argv[2])
    else:
        print("\n".join(drift(sys.argv[1])) or "no drift")
