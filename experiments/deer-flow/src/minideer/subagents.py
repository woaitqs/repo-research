"""Sub-agents: fresh messages, shared sandbox, final text only, no nesting."""

from __future__ import annotations

from typing import Any, Callable

from minideer.messages import HumanMessage, Message
from minideer.middleware import Middleware


class SubagentLimitMiddleware(Middleware):
    def __init__(self, max_concurrent: int):
        self.max_concurrent = max_concurrent

    def after_model(self, state: dict[str, Any], message: Message, ctx: Any) -> dict[str, Any] | None:
        tasks = [item for item in message.tool_calls if item["name"] == "task"]
        if len(tasks) <= self.max_concurrent:
            return None
        kept_ids = {item["id"] for item in tasks[: self.max_concurrent]}
        message.tool_calls = [item for item in message.tool_calls if item["name"] != "task" or item["id"] in kept_ids]
        message.content = (message.content + "\n[SUBAGENT LIMIT]").strip()
        return {"message": message}


def make_task_tool(build_child: Callable[[dict[str, Any]], Any]):
    """Return a tool function. `build_child` receives the parent state."""

    def task(request: Any) -> str:
        parent = request.state
        prompt = request.args["prompt"]
        child = build_child(parent)
        child_state: dict[str, Any] = {
            "messages": [HumanMessage(prompt)],  # ISOLATED_CHILD
            "sandbox": parent.get("sandbox"),
            "summary_text": None,
            "delegations": [],
            "loaded_skills": [],
            "_ctx": child.ctx,
            "_run": {"status": "pending"},
        }
        child.run(child_state)
        finals = [m.content for m in child_state["messages"] if m.role == "ai" and not m.tool_calls]
        text = finals[-1] if finals else ""
        parent.setdefault("delegations", []).append(
            {"id": request.id, "status": "completed", "result_brief": text}
        )
        parent["_child_transcripts"] = parent.get("_child_transcripts", []) + [child_state["messages"]]
        return text

    return task
