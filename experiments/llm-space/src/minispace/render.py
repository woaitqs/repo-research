"""Render doubled-brace placeholders into a request. The stored templates stay."""

from __future__ import annotations

import re
from typing import Any

# Doubled curly braces around a name. The stored thread keeps this text.
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def substitute(text: str, values: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(name)
        return values[name]

    return _PLACEHOLDER.sub(_replace, text)


def render_thread(
    thread: dict[str, Any], values: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (request, snapshot). Existing snapshot places win.

    The caller's thread templates are not written. A later run reuses the
    frozen bytes for places that already have a snapshot entry.
    """
    snapshot = dict(thread.get("snapshot") or {})
    system_stored = thread.get("system", "")
    if "system" in snapshot:  # SNAPSHOT_WINS
        system_rendered = snapshot["system"]
    else:
        system_rendered = substitute(system_stored, values)
        snapshot["system"] = system_rendered

    rendered_messages: list[dict[str, Any]] = []
    for index, message in enumerate(thread.get("messages") or []):
        if message.get("role") != "user":
            rendered_messages.append(message)
            continue
        key = f"m{index}"
        if key in snapshot:
            text = snapshot[key]
        else:
            text = substitute(message.get("text", ""), values)
            snapshot[key] = text
        rendered_messages.append({**message, "text": text})

    request = {"system": system_rendered, "messages": rendered_messages}
    return request, snapshot  # TEMPLATE_UNCHANGED
