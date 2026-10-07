"""Model boundary: items in, items out.

Upstream: `Model.get_response(system_instructions, input, model_settings, tools, output_schema,
handoffs, tracing, ...)` in `src/agents/models/interface.py:37`. The runner never sees a
provider wire format. Each adapter converts items to its API and the reply back to items:
`OpenAIResponsesModel` (native items) and `OpenAIChatCompletionsModel`
(`Converter.items_to_messages`, `src/agents/models/chatcmpl_converter.py:534`).

Handoffs travel as a separate argument and are converted to ordinary function tools at the
wire (`Converter.convert_handoff_tool`, `chatcmpl_converter.py:1040`).
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .items import Item, kind, text_of

if TYPE_CHECKING:
    from .agent import Handoff
    from .tool import FunctionTool


@dataclass
class ModelResponse:
    output: list[Item]
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class ModelCall:
    """What crossed the model boundary on one call (deep-copied for assertions)."""

    system_instructions: str | None
    input: list[Item]
    tools: list[str]
    handoffs: list[str]
    settings: dict[str, Any]


class Model(Protocol):
    async def get_response(
        self,
        *,
        system_instructions: str | None,
        input: list[Item],
        tools: Sequence[FunctionTool],
        handoffs: Sequence[Handoff],
        settings: dict[str, Any],
    ) -> ModelResponse: ...


Step = list[Item] | Exception | Callable[[ModelCall], list[Item]]


class ScriptedModel:
    """Deterministic model, like `agents.testing.ScriptedModel` upstream."""

    def __init__(self, steps: Sequence[Step] = ()) -> None:
        self.steps: list[Step] = list(steps)
        self.calls: list[ModelCall] = []

    async def get_response(self, *, system_instructions, input, tools, handoffs, settings):
        call = ModelCall(
            system_instructions=system_instructions,
            input=copy.deepcopy(list(input)),
            tools=[t.name for t in tools],
            handoffs=[h.tool_name for h in handoffs],
            settings=copy.deepcopy(dict(settings)),
        )
        self.calls.append(call)
        if not self.steps:
            raise AssertionError(f"ScriptedModel ran out of steps at call #{len(self.calls)}")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        output = step(call) if callable(step) else step
        return ModelResponse(output=copy.deepcopy(list(output)))


# ---------------------------------------------------------------------------------------------
# Chat Completions adapter (stdlib HTTP). Used by `real_model/run_mini_ark.py`.
# ---------------------------------------------------------------------------------------------


def items_to_messages(system: str | None, items: list[Item]) -> list[dict[str, Any]]:
    """Convert Responses-style items to Chat Completions messages.

    Consecutive `function_call` items are merged into one assistant message with several
    `tool_calls`, as `Converter.items_to_messages` does upstream.
    """
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    pending_assistant: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal pending_assistant
        if pending_assistant is not None:
            messages.append(pending_assistant)
            pending_assistant = None

    for item in items:
        k = kind(item)
        if k == "function_call":
            if pending_assistant is None:
                pending_assistant = {"role": "assistant", "content": None, "tool_calls": []}
            pending_assistant["tool_calls"].append(
                {
                    "id": item["call_id"],
                    "type": "function",
                    "function": {"name": item["name"], "arguments": item["arguments"]},
                }
            )
            continue
        flush()
        if k == "function_call_output":
            messages.append(
                {"role": "tool", "tool_call_id": item["call_id"], "content": str(item["output"])}
            )
        elif k.startswith("message:"):
            messages.append({"role": item.get("role", "user"), "content": text_of(item)})
        else:
            raise ValueError(f"Chat Completions cannot represent item type {k!r}")
    flush()
    return messages


def tool_param(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def chat_completion_to_items(payload: dict[str, Any]) -> list[Item]:
    message = payload["choices"][0]["message"]
    out: list[Item] = []
    if message.get("content"):
        out.append({"type": "message", "role": "assistant", "content": message["content"]})
    for call in message.get("tool_calls") or []:
        out.append(
            {
                "type": "function_call",
                "name": call["function"]["name"],
                "arguments": call["function"].get("arguments") or "{}",
                "call_id": call["id"],
            }
        )
    return out


class ChatCompletionsModel:
    """Minimal OpenAI-compatible `/chat/completions` adapter (no SDK dependency)."""

    def __init__(self, model: str, *, base_url: str | None = None, api_key: str | None = None):
        self.model = model
        self.base_url = (base_url or os.environ["MINI_BASE_URL"]).rstrip("/")
        self.api_key = api_key or os.environ["MINI_API_KEY"]
        self.calls: list[ModelCall] = []

    def build_request(self, *, system_instructions, input, tools, handoffs, settings):
        body: dict[str, Any] = {
            "model": self.model,
            "messages": items_to_messages(system_instructions, list(input)),
        }
        tool_params = [tool_param(t.name, t.description, t.params_json_schema) for t in tools]
        tool_params += [
            tool_param(h.tool_name, h.tool_description, h.input_json_schema) for h in handoffs
        ]
        if tool_params:
            body["tools"] = tool_params
            if settings.get("tool_choice"):
                body["tool_choice"] = settings["tool_choice"]
        if "temperature" in settings:
            body["temperature"] = settings["temperature"]
        return body

    async def get_response(self, *, system_instructions, input, tools, handoffs, settings):
        self.calls.append(
            ModelCall(
                system_instructions,
                copy.deepcopy(list(input)),
                [t.name for t in tools],
                [h.tool_name for h in handoffs],
                dict(settings),
            )
        )
        body = self.build_request(
            system_instructions=system_instructions,
            input=input,
            tools=tools,
            handoffs=handoffs,
            settings=settings,
        )
        payload = await asyncio.to_thread(self._post, body)
        usage = payload.get("usage") or {}
        return ModelResponse(
            output=chat_completion_to_items(payload),
            usage={k: int(v) for k, v in usage.items() if isinstance(v, int)},
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
