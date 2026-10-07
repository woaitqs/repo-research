"""Model abstraction: one `complete(messages, tools)` boundary.

Mirrors openhands-sdk/openhands/sdk/llm/llm.py at a much smaller scale:
* provider differences live behind one class (the SDK uses LiteLLM);
* transient failures are retried with exponential backoff (RetryMixin);
* provider-specific overflow errors are normalized into one exception type
  (LLMContextWindowExceedError) that the agent loop can act on;
* `ScriptedLLM` plays the role of the SDK's `openhands.sdk.testing.TestLLM`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol


class ContextWindowExceededError(Exception):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string, exactly as the model produced it


@dataclass(frozen=True)
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class LLM(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse: ...


class ScriptedLLM:
    """Deterministic model for tests: pops scripted responses (or raises
    scripted exceptions) and records every request it receives."""

    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self._script: deque[LLMResponse | Exception] = deque(script)
        self.requests: list[list[dict[str, Any]]] = []

    def complete(self, messages, tools=None) -> LLMResponse:
        self.requests.append(messages)
        if not self._script:
            raise RuntimeError("ScriptedLLM exhausted")
        item = self._script.popleft()
        if isinstance(item, Exception):
            raise item
        return item


def tool_call(name: str, args: dict[str, Any] | str, call_id: str | None = None) -> ToolCall:
    raw = args if isinstance(args, str) else json.dumps(args)
    return ToolCall(id=call_id or f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=raw)


class OpenAICompatibleLLM:
    """Chat Completions client over urllib (no third-party deps).

    Works with any OpenAI-compatible endpoint, e.g. Volcano Engine Ark
    (set ARK_BASE_URL / ARK_MODEL / ARK_API_KEY)."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        num_retries: int = 3,
        retry_min_wait: float = 2.0,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.num_retries = num_retries
        self.retry_min_wait = retry_min_wait
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "OpenAICompatibleLLM":
        key = os.environ.get("ARK_API_KEY")
        if not key:
            raise RuntimeError("ARK_API_KEY is not set")
        return cls(
            model=os.environ.get("ARK_MODEL", "doubao-seed-2-1-pro-260915"),
            base_url=os.environ.get(
                "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"
            ),
            api_key=key,
        )

    def complete(self, messages, tools=None) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
        data = json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        last_error: Exception | None = None
        for attempt in range(self.num_retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read())
                return self._parse(payload)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")
                if "context" in detail.lower() and "length" in detail.lower():
                    raise ContextWindowExceededError(detail) from exc
                if exc.code not in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:500]}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            time.sleep(self.retry_min_wait * (2**attempt))
        raise RuntimeError(f"LLM call failed after retries: {last_error}")

    @staticmethod
    def _parse(payload: dict[str, Any]) -> LLMResponse:
        message = payload["choices"][0]["message"]
        calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=tc["function"].get("arguments") or "{}",
            )
            for tc in message.get("tool_calls") or []
        ]
        return LLMResponse(
            text=message.get("content") or "",
            tool_calls=calls,
            id=payload.get("id") or uuid.uuid4().hex[:12],
        )
