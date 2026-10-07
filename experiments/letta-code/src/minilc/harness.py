"""The client harness: drives the backend and owns the execution runtime.

Mirrors the headless one-shot loop (`src/headless.ts:2081-2667`) and the shared
modules it calls (`agent/message.ts`, `cli/helpers/stream.ts`,
`approval-classification.ts`, `agent/approval-execution.ts`):

    content  = <system-reminder> parts + user prompt
    loop:
        chunks = backend.stream(conv, {messages, client_tools, client_skills})
        stop   = last stop_reason
        end_turn          -> done
        requires_approval -> classify (permissions) -> execute batch locally
                             -> next input = [{type: "approval", approvals}]
        anything else     -> error

The harness never talks to a model and the backend never executes a tool:
that split is the architecture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .backend import Backend
from .permissions import PermissionPolicy, classify
from .reminders import reminder_parts
from .tools import ToolContext, ToolRegistry

HEADLESS_DENY = "Tool requires approval (headless mode)"  # headless.ts:2605-2625


class MaxTurnsExceeded(Exception):
    pass


@dataclass
class TurnResult:
    text: str
    stop_reason: str
    steps: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Harness:
    backend: Backend
    agent: dict[str, Any]
    tools: ToolRegistry
    ctx: ToolContext
    policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    skills: list[dict[str, str]] = field(default_factory=list)
    ask_user: Callable[[dict[str, Any]], bool] | None = None  # None = one-shot headless: deny
    max_turns: int = 25
    reminders: bool = True

    def run(self, conversation_id: str, prompt: str) -> TurnResult:
        self.ctx.extras["conversation_id"] = conversation_id  # lets a fork copy this conversation
        content: list[dict[str, Any]] = []
        if self.reminders:
            content += reminder_parts(cwd=self.ctx.cwd, agent_id=self.agent["id"], conversation_id=conversation_id,
                                      memory_dir=self.agent["memory_dir"], permission_mode=self.policy.mode)
        content.append({"type": "text", "text": prompt})
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        result = TurnResult(text="", stop_reason="", steps=0)
        turns = 0
        while True:
            is_continuation = messages[0].get("type") == "approval"
            if not is_continuation:  # max-turns ignores approval continuations (headless.ts:2177-2191)
                turns += 1
                if turns > self.max_turns:
                    raise MaxTurnsExceeded(self.max_turns)
            body = {"messages": messages, "client_tools": self.tools.schemas(), "client_skills": self.skills}
            approvals, stop, texts = [], "", []
            for chunk in self.backend.stream(conversation_id, body):
                result.events.append(chunk)
                kind = chunk["message_type"]
                if kind == "assistant_message":
                    texts.append(chunk["content"])
                elif kind == "approval_request_message":
                    call = chunk["tool_call"]
                    approvals.append({"id": call["tool_call_id"], "name": call["name"],
                                      "arguments": json.loads(call["arguments"] or "{}")})
                elif kind == "stop_reason":
                    stop = chunk["stop_reason"]
            result.steps += 1
            if stop == "end_turn" and approvals:  # stream.ts:456-470
                stop = "requires_approval"
            if stop != "requires_approval":
                result.text, result.stop_reason = "".join(texts), stop
                return result
            result.tool_calls += approvals
            messages = [{"type": "approval", "approvals": self._resolve(approvals)}]

    def _resolve(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """classifyApprovals -> (ask) -> executeApprovalBatch, results in call order."""
        buckets = classify(calls, self.policy)
        approved = [c for c, _ in buckets["allowed"]]
        denied = {c["id"]: reason for c, reason in buckets["denied"]}
        for call, _ in buckets["ask"]:
            if self.ask_user is not None and self.ask_user(call):
                approved.append(call)
            else:
                denied[call["id"]] = HEADLESS_DENY if self.ask_user is None else "User denied the tool call"
        executed = dict(zip([c["id"] for c in approved], self.tools.execute_batch(approved, self.ctx)))
        out = []
        for call in calls:
            if call["id"] in denied:
                out.append({"type": "approval", "tool_call_id": call["id"], "approve": False,
                            "reason": denied[call["id"]]})
            else:
                res = executed[call["id"]]
                out.append({"type": "tool", "tool_call_id": call["id"], **res})
        return out
