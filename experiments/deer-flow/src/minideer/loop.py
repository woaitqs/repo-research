"""The stand-in for LangChain's create_agent loop, plus a run record.

DeerFlow's graph is created by create_agent. The Gateway worker then drives
`agent.astream`. This module keeps those two clocks separate: `_run.status`
is the run, `messages` is the thread.
"""

from __future__ import annotations

from typing import Any, Callable

from minideer.messages import Message, ToolMessage
from minideer.middleware import EndTurn, Middleware, ToolCall
from minideer.model import ModelRequest, ScriptedModel


ToolFn = Callable[[ToolCall], str]


class Agent:
    def __init__(
        self,
        model: ScriptedModel,
        tools: dict[str, ToolFn],
        middlewares: list[Middleware],
        system_prompt: str,
        ctx: Any,
    ):
        self.model = model
        self.tools = tools
        self.middlewares = middlewares
        self.system_prompt = system_prompt
        self.ctx = ctx

    def run(self, state: dict[str, Any], *, user_text: str | None = None, resume: dict[str, str] | None = None) -> dict[str, Any]:
        state.setdefault("messages", [])
        state.setdefault("summary_text", None)
        state.setdefault("delegations", [])
        state.setdefault("loaded_skills", [])
        state["_ctx"] = self.ctx
        run = state.setdefault("_run", {"status": "pending"})
        if state.get("needs_user") and resume is not None:
            raise RuntimeError("clarification ended the run; send a new user message")
        run["status"] = "running"
        state["needs_user"] = False

        if resume is not None:
            pause = state.pop("pause", None)
            if not pause:
                raise RuntimeError("nothing to resume")
            self._run_tools(state, pause["calls"], resume)
        else:
            if user_text is not None:
                from minideer.messages import HumanMessage

                state["messages"].append(HumanMessage(user_text))
            for middleware in self.middlewares:
                middleware.before_agent(state, self.ctx)

        while True:
            for middleware in self.middlewares:
                middleware.before_model(state, self.ctx)
            ai = self._call_model(state)
            denials: list[Message] = []
            pause = None
            for middleware in reversed(self.middlewares):
                effect = middleware.after_model(state, ai, self.ctx)
                if not effect:
                    continue
                if effect.get("message") is not None:
                    ai = effect["message"]
                denials.extend(effect.get("denials") or [])
                if effect.get("pause"):
                    pause = effect["pause"]
            state["messages"].append(ai)
            state["messages"].extend(denials)
            if pause:
                state["pause"] = pause
                run["status"] = "waiting_for_input"
                return state
            if not ai.tool_calls and not denials:
                break
            if self._run_tools(state, ai.tool_calls, None):
                break

        for middleware in self.middlewares:
            middleware.after_agent(state, self.ctx)
        run["status"] = "success"
        return state

    def _call_model(self, state: dict[str, Any]) -> Message:
        request = ModelRequest(system=self.system_prompt, messages=list(state["messages"]), state=state)

        def core(req: ModelRequest) -> Message:
            return self.model.invoke(req)

        handler: Callable[[ModelRequest], Message] = core
        for middleware in reversed(self.middlewares):
            handler = (lambda mw, inner: lambda req: mw.wrap_model_call(req, inner))(middleware, handler)
        return handler(request)

    def _run_tools(self, state: dict[str, Any], calls: list[dict[str, Any]], decisions: dict[str, str] | None) -> bool:
        """Return True when the turn must stop (clarification)."""
        for item in calls:
            decision = None if decisions is None else decisions.get(item["id"], "approve")
            if decision == "reject":
                state["messages"].append(
                    ToolMessage(
                        "rejected by human",
                        tool_call_id=item["id"],
                        name=item["name"],
                        status="error",
                    )
                )
                continue

            def core(req: ToolCall, tool_name: str = item["name"]) -> Message:
                fn = self.tools.get(tool_name)
                if fn is None:
                    return ToolMessage(f"unknown tool {tool_name}", tool_call_id=req.id, name=tool_name, status="error")
                try:
                    content = fn(req)
                except Exception as exc:  # observations, not crashes
                    return ToolMessage(f"error: {exc}", tool_call_id=req.id, name=tool_name, status="error")
                return ToolMessage(content, tool_call_id=req.id, name=tool_name)

            handler: Callable[[ToolCall], Message] = core
            for middleware in reversed(self.middlewares):
                handler = (lambda mw, inner: lambda req: mw.wrap_tool_call(req, inner))(middleware, handler)
            request = ToolCall(item["name"], item.get("args") or {}, item["id"], state, self.ctx)
            try:
                result = handler(request)
            except EndTurn as stop:
                state["messages"].append(stop.message)
                state["needs_user"] = True
                state["_run"]["status"] = "success"
                return True
            state["messages"].append(result)
        return False
