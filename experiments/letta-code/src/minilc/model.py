"""Model abstraction: one call = one assistant step.

letta-code delegates provider details to pi-ai (`src/backend/dev/pi-stream-adapter.ts`);
the harness only needs "given system + messages + tools, produce one assistant
message". `ScriptedModel` makes every request inspectable; `OpenAICompatModel`
talks to any OpenAI-compatible Chat Completions endpoint with the stdlib.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any, Callable

Step = dict[str, Any]  # {"text", "tool_calls": [{"id","name","arguments"}], "stop", "usage"}


class ContextOverflowError(Exception):
    """Provider rejected the request because it does not fit the window."""


class TransientProviderError(Exception):
    """Retryable transport/provider failure."""


class ScriptedModel:
    """Plays back scripted steps and records each request it receives."""

    def __init__(self, script: list[Step | Callable[[dict[str, Any]], Step]], context_window: int = 200_000):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.context_window = context_window

    def complete(self, request: dict[str, Any]) -> Step:
        self.requests.append(json.loads(json.dumps(request)))
        if not self.script:
            return {"text": "(script exhausted)", "tool_calls": [], "stop": "stop"}
        step = self.script.pop(0)
        result = step(request) if callable(step) else step
        if isinstance(result, Exception):
            raise result
        return {"tool_calls": [], "stop": "tool" if result.get("tool_calls") else "stop", **result}


def _to_openai(request: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": request["system"]}]
    for m in request["messages"]:
        if m["role"] == "user":
            content = m["content"]
            messages.append({"role": "user", "content": content if isinstance(content, str)
                             else "\n\n".join(p["text"] for p in content)})
        elif m["role"] == "system":  # transient mid-conversation update
            messages.append({"role": "system", "content": m["content"]})
        elif m["role"] == "assistant":
            out: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                out["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                                     for c in m["tool_calls"]]
            messages.append(out)
        elif m["role"] == "toolResult":
            messages.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
    tools = [{"type": "function", "function": t} for t in request.get("tools", [])]
    return {"messages": messages, **({"tools": tools} if tools else {})}


class OpenAICompatModel:
    def __init__(self, base_url: str, api_key: str, model: str, context_window: int = 128_000):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.context_window = context_window
        self.requests: list[dict[str, Any]] = []
        cafile = os.environ.get("MINILC_CA_BUNDLE")
        self._ssl = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

    def complete(self, request: dict[str, Any]) -> Step:
        self.requests.append(request)
        body = {"model": self.model, **_to_openai(request)}
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, context=self._ssl, timeout=300) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")
            if "context" in detail.lower() and "length" in detail.lower():
                raise ContextOverflowError(detail) from err
            if err.code in (429, 500, 502, 503, 504):
                raise TransientProviderError(detail) from err
            raise
        choice = data["choices"][0]
        message = choice["message"]
        calls = [{"id": c["id"], "name": c["function"]["name"],
                  "arguments": json.loads(c["function"]["arguments"] or "{}")}
                 for c in message.get("tool_calls") or []]
        stop = "tool" if calls else ("length" if choice.get("finish_reason") == "length" else "stop")
        return {"text": message.get("content") or "", "tool_calls": calls, "stop": stop, "usage": data.get("usage", {})}
