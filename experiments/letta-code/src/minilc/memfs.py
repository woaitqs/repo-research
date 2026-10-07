"""Git-backed memory filesystem ("MemFS"), compiled into the system prompt.

Mirrors `src/backend/local/system-prompt-compilation.ts` (memfs-v2 layout):

* Root `*.md` files (except `MEMORY.md`) are *core memory*: rendered in full as
  `<label><description>...</description>body</label>`.
* Root `MEMORY.md` is an index rendered inside `<memory>`; a child directory
  with its own `MEMORY.md` is listed as a deferred `<directory .../>` that the
  agent opens on demand (progressive disclosure).
* Only **committed** content counts: files are read with `git show HEAD:path`,
  so an uncommitted edit never reaches the model. A commit is the publish step.
"""

from __future__ import annotations

import os
import re
import subprocess

CORE_MEMORY_VARIABLE = "{CORE_MEMORY}"


def _git(memory_dir: str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=memory_dir, check=True, capture_output=True, text=True
    ).stdout


def init_memory_repo(memory_dir: str, files: dict[str, str]) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    if not os.path.isdir(os.path.join(memory_dir, ".git")):
        _git(memory_dir, "init", "-q")
    for rel, text in files.items():
        path = os.path.join(memory_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    commit(memory_dir, "chore: initialize memory")


def commit(memory_dir: str, message: str) -> None:
    _git(memory_dir, "add", "-A")
    _git(memory_dir, "-c", "user.name=minilc", "-c", "user.email=minilc@example.com",
         "commit", "-q", "--allow-empty", "-m", message)


def committed_revision(memory_dir: str) -> str | None:
    try:
        return _git(memory_dir, "rev-parse", "--verify", "HEAD").strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _frontmatter(raw: str) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    if not match:
        return {}, raw
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"')
    return meta, match.group(2)


def render_core_memory(memory_dir: str) -> tuple[str, str | None]:
    """Render committed memory. Returns (core_memory_text, revision)."""
    revision = committed_revision(memory_dir)
    if revision is None:
        return "", None
    paths = [p for p in _git(memory_dir, "ls-tree", "-r", "--name-only", "HEAD").splitlines() if p.endswith(".md")]
    blocks: list[str] = []
    for rel in sorted(p for p in paths if "/" not in p and p != "MEMORY.md"):
        meta, body = _frontmatter(_git(memory_dir, "show", f"HEAD:{rel}"))
        label = rel[:-3]
        lines = [f"<{label}>"]
        if meta.get("description"):
            lines.append(f"<description>{meta['description']}</description>")
        if body.strip():
            lines.append(body.rstrip())
        lines.append(f"</{label}>")
        blocks.append("\n".join(lines))
    if "MEMORY.md" in paths:
        lines = ["<memory>", _git(memory_dir, "show", "HEAD:MEMORY.md").rstrip()]
        children = sorted(p for p in paths if re.fullmatch(r"[^/]+/MEMORY\.md", p) and not p.startswith("skills/"))
        if children:
            lines.append("<deferred-memory>")
            lines += [f'<directory path="{c[:-10]}/" index="{c}" />' for c in children]
            lines.append("</deferred-memory>")
        lines.append("</memory>")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), revision


def compile_system_prompt(raw_system: str, memory_dir: str | None) -> dict[str, str | None]:
    core, revision = render_core_memory(memory_dir) if memory_dir else ("", None)
    template = raw_system if CORE_MEMORY_VARIABLE in raw_system else f"{raw_system.rstrip()}\n\n{CORE_MEMORY_VARIABLE}"
    return {"content": template.replace(CORE_MEMORY_VARIABLE, core), "core_memory": core, "revision": revision}


def memory_update_delta(core_memory: str, revision: str | None) -> str:
    """The one-shot mid-conversation update (local-backend.ts:165-176)."""
    return "\n".join([
        "<memory_update>",
        f"The memory filesystem has been edited and committed at revision {revision}.",
        "Treat the following freshly rendered memory context as authoritative from now on.",
        "",
        core_memory.rstrip(),
        "</memory_update>",
    ])
