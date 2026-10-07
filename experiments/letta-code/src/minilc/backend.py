"""The stateful backend: owns agents, conversations, memory and compaction.

letta-code has one `Backend` interface (`src/backend/backend.ts:190-369`) typed
against the Letta REST client, with two implementations: `APIBackend` (Letta
Cloud over HTTP) and the in-process `LocalBackend`. The local one emulates the
server's run semantics (`src/backend/dev/fake-headless-backend.ts:469-776`):

* one active run per conversation;
* a run executes **one model step**; if the model asks for tools, every call is
  emitted as `approval_request_message` and the run stops with
  `stop_reason="requires_approval"` - tools are never executed here;
* the client resumes by sending `{"type": "approval", "approvals": [...]}`;
* tool calls left dangling by an interrupted turn get a synthetic error result
  before the next user turn (providers reject orphan tool_use blocks).

The client only ever sees the `Backend` methods below, so swapping
`LocalBackend` for a remote server does not change the harness.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
from typing import Any, Iterator, Protocol

from . import compaction, memfs
from .memfs import init_memory_repo as memfs_init
from .model import ContextOverflowError, TransientProviderError
from .transcript import Transcript

Chunk = dict[str, Any]
TURN_DID_NOT_COMPLETE = "Turn did not complete"
MAX_OVERFLOW_COMPACTIONS = 3  # pi-stream-adapter.ts:64
MAX_TRANSIENT_RETRIES = 3     # pi-stream-adapter.ts:61
SUMMARY_PROMPT = ("The following messages are being evicted from the BEGINNING of your context window. "
                  "Write a detailed summary of goals, what happened, important details (verbatim identifiers), "
                  "errors and fixes, and lookup hints. Only output the summary.")


class Backend(Protocol):
    def create_agent(self, name: str, system: str, memory_files: dict[str, str] | None = None,
                     memfs: bool = True) -> dict: ...
    def create_conversation(self, agent_id: str) -> str: ...
    def stream(self, conversation_id: str, body: dict[str, Any]) -> Iterator[Chunk]: ...
    def compact(self, conversation_id: str, mode: str | None = None) -> dict: ...
    def recompile(self, conversation_id: str) -> str: ...


class ActiveRunError(RuntimeError):
    pass


class LocalBackend:
    def __init__(self, storage_dir: str, model: Any, compaction_mode: str = "sliding_window"):
        self.storage_dir = storage_dir
        self.model = model
        self.compaction_mode = compaction_mode
        self.agents: dict[str, dict[str, Any]] = {}
        self.conversations: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self.runs: list[dict[str, Any]] = []

    # -- agents / conversations ---------------------------------------------
    def create_agent(self, name: str, system: str, memory_files: dict[str, str] | None = None,
                     memfs: bool = True) -> dict:
        agent_id = f"agent-{next(self._ids)}"
        memory_dir = None
        if memfs:  # fresh sub-agents are created without MemFS (subagents/manager.ts:194-196)
            memory_dir = os.path.join(self.storage_dir, "memfs", agent_id, "memory")
            memfs_init(memory_dir, memory_files or {"MEMORY.md": "# Memory\n"})
        self.agents[agent_id] = {"id": agent_id, "name": name, "system": system, "memory_dir": memory_dir}
        return self.agents[agent_id]

    def create_conversation(self, agent_id: str, fork_of: str | None = None) -> str:
        conv_id = f"conv-{next(self._ids)}"
        transcript = Transcript(os.path.join(self.storage_dir, "conversations", conv_id, "messages.jsonl"))
        if fork_of:  # a forked child starts from a copy of the parent's in-context view
            for message in self.conversations[fork_of]["transcript"].view():
                extra = {k: v for k, v in message.items() if k not in ("id", "role", "content")}
                transcript.append(message["role"], message["content"], **extra)
        self.conversations[conv_id] = {"agent_id": agent_id, "transcript": transcript,
                                       "compiled": None, "active_run": None}
        self.recompile(conv_id)  # compiled once at creation, then cached
        return conv_id

    def transcript(self, conversation_id: str) -> Transcript:
        return self.conversations[conversation_id]["transcript"]

    # -- system prompt: cached, cache-stable, delta on memory change -------
    def recompile(self, conversation_id: str) -> str:
        conv = self.conversations[conversation_id]
        agent = self.agents[conv["agent_id"]]
        compiled = memfs.compile_system_prompt(agent["system"], agent["memory_dir"])
        compiled["raw_hash"] = hashlib.sha256(agent["system"].encode()).hexdigest()
        conv["compiled"] = compiled
        return compiled["content"]

    def _resolve_prompt(self, conversation_id: str) -> tuple[str, str | None]:
        """local-backend.ts:896-947: reuse the cached prompt; if only the committed
        memory revision moved, keep the old prompt bytes and return a one-shot
        `<memory_update>` delta, advancing the stored revision."""
        conv = self.conversations[conversation_id]
        agent = self.agents[conv["agent_id"]]
        cached = conv["compiled"]
        revision = memfs.committed_revision(agent["memory_dir"]) if agent["memory_dir"] else None
        raw_hash = hashlib.sha256(agent["system"].encode()).hexdigest()
        if cached["raw_hash"] == raw_hash and cached["revision"] == revision:
            return cached["content"], None
        if cached["raw_hash"] == raw_hash:
            fresh = memfs.compile_system_prompt(agent["system"], agent["memory_dir"])
            conv["compiled"] = {**cached, "core_memory": fresh["core_memory"], "revision": fresh["revision"]}
            return cached["content"], memfs.memory_update_delta(fresh["core_memory"], fresh["revision"])
        return self.recompile(conversation_id), None

    # -- the run ---------------------------------------------------------
    def stream(self, conversation_id: str, body: dict[str, Any]) -> Iterator[Chunk]:
        """Eager part (like executeConversationTurn): validate, settle, append input,
        start the run. Returns a generator that performs the single model step."""
        conv = self.conversations[conversation_id]
        if conv["active_run"] is not None:
            raise ActiveRunError(f"Conversation {conversation_id} already has an active run ({conv['active_run']})")
        transcript: Transcript = conv["transcript"]
        messages = body.get("messages", [])
        if not any(m.get("type") == "approval" for m in messages):
            for call in transcript.pending_tool_calls():
                transcript.append("toolResult", TURN_DID_NOT_COMPLETE, tool_call_id=call["id"],
                                  tool_name=call["name"], is_error=True)
        for message in messages:
            if message.get("type") == "approval":
                self._apply_approvals(transcript, message.get("approvals", []))
            elif message.get("role") == "user":
                transcript.append("user", message["content"])
        run = {"id": f"run-{len(self.runs) + 1}", "conversation_id": conversation_id, "status": "running"}
        self.runs.append(run)
        conv["active_run"] = run["id"]
        return self._run(conversation_id, conv, run, body)

    @staticmethod
    def _apply_approvals(transcript: Transcript, approvals: list[dict[str, Any]]) -> None:
        for approval in approvals:
            call_id = approval.get("tool_call_id")
            call = next((c for m in transcript.view() if m["role"] == "assistant"
                         for c in m.get("tool_calls", []) if c["id"] == call_id), None)
            if call is None or transcript.tool_result_for(call_id):
                continue
            if approval.get("type") == "approval" and approval.get("approve") is False:
                transcript.append("toolResult", approval.get("reason") or "Tool execution denied.",
                                  tool_call_id=call_id, tool_name=call["name"], is_error=True)
            elif approval.get("type") == "tool":
                # Upstream stores client tool results with isError=false whatever
                # `status` says (local-store.ts:1538-1545); errors travel as text.
                transcript.append("toolResult", approval.get("tool_return", ""), tool_call_id=call_id,
                                  tool_name=call["name"], is_error=False)

    def _request(self, conv: dict[str, Any], body: dict[str, Any], system: str, delta: str | None) -> dict[str, Any]:
        view = [dict(m) for m in conv["transcript"].view()]
        if delta:  # transient: appended to this provider call only, never persisted
            view.append({"role": "system", "content": delta})
        skills = body.get("client_skills") or []
        if skills:
            lines = [f"- `{s['name']}`: {s.get('description', '').splitlines()[0] if s.get('description') else ''}"
                     for s in sorted(skills, key=lambda s: s["name"])]
            system = f"{system.rstrip()}\n\n<available_skills>\n" + "\n".join(lines) + "\n</available_skills>"
        return {"system": system, "messages": view, "tools": body.get("client_tools") or []}

    def _run(self, conversation_id: str, conv: dict[str, Any], run: dict[str, Any], body: dict[str, Any]) -> Iterator[Chunk]:
        try:
            system, delta = self._resolve_prompt(conversation_id)
            request = self._request(conv, body, system, delta)
            window = getattr(self.model, "context_window", 200_000)
            if compaction.should_compact(compaction.estimate_tokens(request), window):  # preflight
                yield from self._compact_chunks(conversation_id, "context_window_limit")
                system, delta = conv["compiled"]["content"], None
                request = self._request(conv, body, system, delta)
            overflows = retries = 0
            while True:
                try:
                    step = self.model.complete(request)
                    break
                except ContextOverflowError:
                    if overflows >= MAX_OVERFLOW_COMPACTIONS:
                        raise
                    overflows += 1
                    yield from self._compact_chunks(conversation_id, "context_window_overflow")
                    request = self._request(conv, body, conv["compiled"]["content"], None)
                except TransientProviderError as err:
                    if retries >= MAX_TRANSIENT_RETRIES:
                        raise
                    retries += 1
                    yield {"message_type": "event_message", "event_type": "retry",
                           "event_data": {"attempt": retries, "message": str(err)}}
            calls = step.get("tool_calls") or []
            conv["transcript"].append("assistant", step.get("text") or "", tool_calls=calls)
            if step.get("text"):
                yield {"message_type": "assistant_message", "content": step["text"]}
            for call in calls:
                yield {"message_type": "approval_request_message",
                       "tool_call": {"tool_call_id": call["id"], "name": call["name"],
                                     "arguments": json.dumps(call["arguments"])}}
            yield {"message_type": "usage_statistics", **(step.get("usage") or {})}
            stop = "requires_approval" if calls else ("max_tokens_exceeded" if step.get("stop") == "length" else "end_turn")
            run["status"] = "completed"
            yield {"message_type": "stop_reason", "stop_reason": stop}
        except Exception as err:  # noqa: BLE001 - surfaced as a protocol error, like failRun
            run["status"] = "failed"
            yield {"message_type": "error_message", "message": str(err)}
            yield {"message_type": "stop_reason", "stop_reason": "error"}
        finally:
            conv["active_run"] = None

    # -- compaction ----------------------------------------------------------
    def _compact_chunks(self, conversation_id: str, trigger: str) -> Iterator[Chunk]:
        result = self.compact(conversation_id, trigger=trigger)
        yield {"message_type": "event_message", "event_type": "compaction", "event_data": {"trigger": trigger}}
        yield {"message_type": "summary_message", "summary": result["summary"]}

    def compact(self, conversation_id: str, mode: str | None = None, trigger: str = "manual") -> dict:
        conv = self.conversations[conversation_id]
        transcript: Transcript = conv["transcript"]
        view = transcript.view()
        mode = mode or self.compaction_mode
        window = getattr(self.model, "context_window", 200_000)
        try:
            if mode != "sliding_window":
                raise compaction.PlanningError("all requested")
            to_summarize, to_keep = compaction.plan_sliding_window(view, window)
        except compaction.PlanningError:
            mode = "all"
            to_summarize, to_keep = compaction.plan_all(view)
        rendered = "\n".join(f"{m['role']}: {json.dumps(m['content'])[:2000]}" for m in to_summarize)
        summary = self.model.complete({"system": SUMMARY_PROMPT,
                                       "messages": [{"role": "user", "content": rendered}], "tools": []})["text"]
        packed = compaction.package_summary(summary, len(to_summarize), mode)
        summary_msg = {"id": f"msg-summary-{len(transcript.rows())}", "role": "user", "content": packed,
                       "compaction": {"summary": summary, "trigger": trigger}}
        transcript.append_compaction(summary_msg, to_keep[0]["id"] if to_keep else None, [m["id"] for m in to_keep])
        self.recompile(conversation_id)  # local-backend.ts:760-786: compaction re-renders the prompt
        return {"summary": summary, "num_messages_before": len(view), "num_messages_after": 1 + len(to_keep)}
