"""Preview-first compaction. Applying it builds a new message list."""

from __future__ import annotations

import re
from typing import Any

PREFIX = (
    "<system-reminder>\n"
    "The earlier conversation was compacted into the checkpoint below. "
    "Use it as context to continue the task; it is not a new user request.\n\n"
    "# Context checkpoint\n\n"
)
SUFFIX = "\n</system-reminder>"
LEGACY = "# Context checkpoint\n\n"
_COMPACT_SUFFIX = re.compile(r"-compact-(\d+)$")


def is_checkpoint(message: dict[str, Any]) -> bool:
    text = message.get("text") or ""
    return text.startswith(PREFIX) or text.startswith(LEGACY)


def unwrap(text: str) -> str:
    if text.startswith(PREFIX):
        body = text[len(PREFIX) :]
        if body.endswith(SUFFIX):
            body = body[: -len(SUFFIX)]
        return body.strip()
    if text.startswith(LEGACY):
        return text[len(LEGACY) :].strip()
    return text.strip()


def plan_compaction(messages: list[dict[str, Any]], keep_recent_turns: int) -> dict[str, Any]:
    keep = max(0, int(keep_recent_turns))
    meta = None
    rest = list(messages)
    if rest and rest[0].get("meta") and not is_checkpoint(rest[0]):
        meta = rest[0]
        rest = rest[1:]
    previous = None
    checkpoint_id = None
    if rest and is_checkpoint(rest[0]):
        previous = unwrap(rest[0]["text"])
        checkpoint_id = rest[0].get("id")
        rest = rest[1:]
    user_indexes = [
        index for index, message in enumerate(rest) if message.get("role") == "user"
    ]
    kept_turns = min(keep, len(user_indexes))
    if kept_turns == 0:
        first_kept = len(rest)
    else:
        first_kept = user_indexes[len(user_indexes) - kept_turns]
    return {
        "meta": meta,
        "to_summarize": rest[:first_kept],
        "kept": rest[first_kept:],
        "previous": previous,
        "checkpoint_id": checkpoint_id,
        "turn_count": len(user_indexes),
        "kept_turn_count": kept_turns,
    }


def summarizer_prompt(plan: dict[str, Any], instructions: str = "") -> str:
    lines: list[str] = []
    for message in plan["to_summarize"]:
        lines.append(f"[{message.get('role')}]: {message.get('text', '')}")
        for call in message.get("tool_calls") or []:
            output = call.get("output") or ""
            if len(output) > 2000:
                output = output[:2000] + "\n\n[truncated]"
            lines.append(f"[tool {call['name']}]: {output}")
    previous = ""
    if plan["previous"]:
        previous = f"\n\n<previous-checkpoint>\n{plan['previous']}\n</previous-checkpoint>"
    focus = f"\n\nAdditional focus: {instructions.strip()}" if instructions.strip() else ""
    body = "\n\n".join(lines) or "(no new messages)"
    return f"<conversation>\n{body}\n</conversation>{previous}{focus}"


def apply_compaction(plan: dict[str, Any], summary: str) -> list[dict[str, Any]]:
    # COMPACT_COPY: the caller's message list is left as it was.
    checkpoint = {
        "id": plan["checkpoint_id"] or "checkpoint",
        "role": "user",
        "text": f"{PREFIX}{summary.strip()}{SUFFIX}",
        "checkpoint": True,
    }
    applied: list[dict[str, Any]] = []
    if plan["meta"] is not None:
        applied.append(plan["meta"])
    applied.append(checkpoint)
    applied.extend(plan["kept"])
    return applied


def next_compact_path(current: str, existing: set[str]) -> str:
    parent, _, name = current.rpartition("/")
    stem = name[:-5] if name.endswith(".json") else name
    base = _COMPACT_SUFFIX.sub("", stem)
    prefix = f"{base}-compact-"
    highest = 0
    for item in existing:
        item_name = item.rpartition("/")[2]
        if not item_name.startswith(prefix) or not item_name.endswith(".json"):
            continue
        number = item_name[len(prefix) : -5]
        if number.isdigit():
            highest = max(highest, int(number))
    current_match = _COMPACT_SUFFIX.search(stem)
    current_number = int(current_match.group(1)) if current_match else 0
    number = max(highest, current_number) + 1
    filename = f"{base}-compact-{number}.json"
    return f"{parent}/{filename}" if parent else filename
