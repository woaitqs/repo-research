"""Summarize per-call provider usage from ark_shim.py request logs.

The shim logs every request body and the raw SSE response. This script keeps only
what is needed to check cache behaviour and window pressure: the request shape and
the provider-reported usage. No message text is copied.

    python3 extract_usage.py run1=/tmp/letta-real-XXXX/shim.jsonl ... > provider_usage.json
"""

from __future__ import annotations

import hashlib
import json
import sys


def _system_text(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        if m.get("role") == "system":
            c = m.get("content")
            parts.append(c if isinstance(c, str) else json.dumps(c, sort_keys=True))
    return "\n".join(parts)


def _last_usage(raw: str) -> tuple[dict, str | None]:
    usage, finish = {}, None
    for line in raw.splitlines():
        if not line.startswith("data: {"):
            continue
        chunk = json.loads(line[len("data: "):])
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            finish = choice.get("finish_reason") or finish
    return usage, finish


def summarize(path: str) -> list[dict]:
    calls = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            body = row.get("request") or {}
            messages = body.get("messages", [])
            system = _system_text(messages)
            usage, finish = _last_usage(row.get("response_raw", ""))
            calls.append(
                {
                    "seq": row.get("seq"),
                    "n_messages": len(messages),
                    "n_tools": len(body.get("tools", [])),
                    "system_sha12": hashlib.sha256(system.encode()).hexdigest()[:12],
                    "memory_update_in_system": "<memory_update>" in system,
                    "max_completion_tokens": body.get("max_completion_tokens"),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                    "finish_reason": finish,
                }
            )
    return calls


def main(argv: list[str]) -> None:
    out = {
        "note": "Per provider call, in order, for each scripted run. prompt_tokens includes cached_tokens.",
        "runs": {},
    }
    for arg in argv:
        name, _, path = arg.partition("=")
        out["runs"][name] = summarize(path)
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1:])
