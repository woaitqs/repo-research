"""SummarizationMiddleware: bounded context over unbounded history, without deleting it.

Mirrors deepagents/middleware/summarization.py::_DeepAgentsSummarizationMiddleware:

- State keeps the FULL message log. Compaction is recorded as a private
  `_summarization_event = {cutoff_index, summary_message, file_path}`.
- Every model call rebuilds the *effective* view:  [summary] + messages[cutoff:]
  (`_apply_event_to_messages`). Stored state is never rewritten.
- Before summarizing, the evicted span is appended to
  `/conversation_history/<session_id>.md` on the backend and the summary message
  points at that file, so details stay recoverable with `read_file`.
- The cutoff never separates an AIMessage from its ToolMessages
  (LangChain `_find_safe_cutoff_point`).
- Reactive path: if the provider rejects the request as too long
  (`ContextOverflowError`), summarize and retry once.
"""

from __future__ import annotations

import uuid
from typing import Any

from minideep.backends import BackendProtocol, StateBackend, artifacts_root
from minideep.messages import AIMessage, HumanMessage, Message, ToolMessage, count_tokens
from minideep.middleware import Middleware, ModelHandler, ModelRequest, ModelResponse, with_update
from minideep.model import ChatModel, ContextOverflowError

EVENT_KEY = "_summarization_event"
SESSION_KEY = "_summarization_session_id"
SUMMARY_PROMPT = "Summarize the conversation below so work can continue. Keep file paths, decisions and open tasks.\n\n<messages>\n{messages}\n</messages>"


def apply_event(messages: list[Message], event: dict[str, Any] | None) -> list[Message]:
    if not event:
        return list(messages)
    return [event["summary_message"], *messages[event["cutoff_index"] :]]


def safe_cutoff(messages: list[Message], cutoff: int) -> int:
    """Move the cutoff back so an AIMessage and its ToolMessages stay together."""
    if cutoff >= len(messages) or not isinstance(messages[cutoff], ToolMessage):
        return cutoff
    ids = set()
    i = cutoff
    while i < len(messages) and isinstance(messages[i], ToolMessage):
        ids.add(messages[i].tool_call_id)
        i += 1
    for j in range(cutoff - 1, -1, -1):
        m = messages[j]
        if isinstance(m, AIMessage) and ids & {tc.id for tc in m.tool_calls}:
            return j
    return i


def _render(messages: list[Message]) -> str:
    out = []
    for m in messages:
        calls = f" tool_calls={[(c.name, c.args) for c in m.tool_calls]}" if isinstance(m, AIMessage) and m.tool_calls else ""
        out.append(f"<{m.role}>{m.content}{calls}</{m.role}>")
    return "\n".join(out)


class SummarizationMiddleware(Middleware):
    def __init__(
        self,
        model: ChatModel,
        backend: BackendProtocol | None = None,
        *,
        trigger_tokens: int | None = None,
        keep_messages: int = 6,
        trim_tokens_to_summarize: int | None = None,
    ) -> None:
        self.model = model
        self.backend = backend or StateBackend()
        self.trim_tokens_to_summarize = trim_tokens_to_summarize
        # Upstream default: 85% of the model's context window when known, else 170k tokens.
        window = getattr(model, "max_input_tokens", None)
        self.trigger_tokens = trigger_tokens or (int(window * 0.85) if window else 170_000)
        self.keep_messages = keep_messages
        self.history_prefix = f"{artifacts_root(self.backend)}/conversation_history"

    def wrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        event = request.state.get(EVENT_KEY)
        effective = apply_event(request.messages, event)
        if count_tokens(effective, request.system_prompt) < self.trigger_tokens:
            try:
                return handler(request.override(messages=effective))
            except ContextOverflowError:
                pass  # reactive fallback: fall through and summarize
        return self._summarize_and_call(request, handler, effective, event)

    def _summarize_and_call(
        self, request: ModelRequest, handler: ModelHandler, effective: list[Message], event: dict[str, Any] | None
    ) -> ModelResponse:
        cutoff = safe_cutoff(effective, max(len(effective) - self.keep_messages, 0))
        if cutoff <= 0:
            return handler(request.override(messages=effective))
        to_summarize, preserved = effective[:cutoff], effective[cutoff:]
        session = request.state.get(SESSION_KEY) or f"session_{uuid.uuid4().hex}"
        path = self._offload(to_summarize, session)  # the full span is saved, even if trimmed below
        summary = self.model.invoke([HumanMessage(SUMMARY_PROMPT.format(messages=_render(self._trim(to_summarize))))], []).content
        summary_msg = HumanMessage(
            "You are in the middle of a conversation that has been summarized.\n\n"
            f"The full conversation history has been saved to {path} should you need to refer back to it.\n\n"
            f"<summary>\n{summary}\n</summary>",
            meta={"lc_source": "summarization"},
        )
        # Translate the effective-list cutoff into an index on the raw state list.
        # Effective index 0 is the previous summary, which is not a real state message.
        state_cutoff = cutoff if not event else event["cutoff_index"] + cutoff - 1
        new_event = {"cutoff_index": state_cutoff, "summary_message": summary_msg, "file_path": path}
        response = handler(request.override(messages=[summary_msg, *preserved]))
        return with_update(response, {EVENT_KEY: new_event, SESSION_KEY: session})

    def _trim(self, messages: list[Message]) -> list[Message]:
        """Keep only the most recent messages that fit the summarizer budget (LangChain `trim_tokens_to_summarize`)."""
        if self.trim_tokens_to_summarize is None:
            return messages
        kept: list[Message] = []
        for m in reversed(messages):
            if count_tokens([*kept, m]) > self.trim_tokens_to_summarize:
                break
            kept.insert(0, m)
        return kept

    def _offload(self, messages: list[Message], session: str) -> str | None:
        """Append evicted messages (minus earlier summaries) to one history file per session."""
        path = f"{self.history_prefix}/{session}.md"
        body = _render([m for m in messages if m.meta.get("lc_source") != "summarization"])
        try:
            try:
                existing = self.backend.read(path)
            except FileNotFoundError:
                existing = ""
            self.backend.write(path, f"{existing}## Summarized section\n\n{body}\n\n")
        except Exception:  # noqa: BLE001 - offload failure must not block summarization
            return None
        return path
