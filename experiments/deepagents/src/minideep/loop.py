"""The agent loop.

deepagents does not own its loop: `create_deep_agent` delegates to LangChain's
`create_agent` (langchain/agents/factory.py), which compiles a LangGraph graph:

    START -> [before_agent hooks] -> model -> (tool calls?) -> tools -> model -> ... -> END

Each `model` step builds a *fresh* `ModelRequest` from state, runs it through the
composed `wrap_model_call` chain, and appends the AIMessage. If the AIMessage has tool
calls, each call is dispatched (upstream: one `Send("tools", [call])` per call) through
the composed `wrap_tool_call` chain. No tool calls => exit.

This module reproduces that control flow in ~100 lines, sequentially and in-process.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from typing import Any

from minideep.backends import RunContext, run_context
from minideep.messages import AIMessage, Command, SystemMessage, ToolMessage, apply_update
from minideep.middleware import Middleware, ModelHandler, ModelRequest, ModelResponse, ToolCallRequest, ToolHandler
from minideep.model import ChatModel
from minideep.tools import Tool, ToolRuntime

Tracer = Callable[..., None]


def _compose_model(middleware: Sequence[Middleware], core: ModelHandler) -> ModelHandler:
    handler = core
    for m in reversed(middleware):  # first middleware ends up outermost
        handler = (lambda mw, inner: lambda req: mw.wrap_model_call(req, inner))(m, handler)
    return handler


def _compose_tool(middleware: Sequence[Middleware], core: ToolHandler) -> ToolHandler:
    handler = core
    for m in reversed(middleware):
        handler = (lambda mw, inner: lambda req: mw.wrap_tool_call(req, inner))(m, handler)
    return handler


class Agent:
    def __init__(
        self,
        model: ChatModel,
        *,
        system_prompt: str = "",
        tools: Sequence[Tool] = (),
        middleware: Sequence[Middleware] = (),
        name: str = "agent",
        max_steps: int = 200,
        tracer: Tracer | None = None,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt
        self.middleware = list(middleware)
        self.name = name
        self.max_steps = max_steps
        self.tracer = tracer or (lambda *a, **k: None)
        # Middleware tools first, then caller tools (same order deepagents exposes).
        self.tools = [t for m in self.middleware for t in m.tools] + list(tools)
        self._tools_by_name = {t.name: t for t in self.tools}
        self._model_chain = _compose_model(self.middleware, self._call_model)
        self._tool_chain = _compose_tool(self.middleware, self._run_tool)

    # -- innermost handlers -------------------------------------------------
    def _call_model(self, request: ModelRequest) -> ModelResponse:
        prompt = [SystemMessage(request.system_prompt)] if request.system_prompt else []
        self.tracer(
            "model_call",
            agent=self.name,
            messages=len(request.messages),
            tools=[t.name for t in request.tools],
            system_chars=len(request.system_prompt),
        )
        return ModelResponse(request.model.invoke(prompt + request.messages, request.tools))

    def _run_tool(self, request: ToolCallRequest) -> ToolMessage | Command:
        call = request.call
        if request.tool is None:
            return ToolMessage(f"Error: tool '{call.name}' is not available.", tool_call_id=call.id, name=call.name, status="error")
        try:
            result = request.tool.run(call.args, ToolRuntime(state=request.state, tool_call_id=call.id))
        except Exception as exc:  # noqa: BLE001 - errors become observations the model can react to
            return ToolMessage(f"Error: {type(exc).__name__}: {exc}", tool_call_id=call.id, name=call.name, status="error")
        if isinstance(result, Command):
            return result
        return ToolMessage(str(result), tool_call_id=call.id, name=call.name)

    # -- graph nodes --------------------------------------------------------
    def _node(self, state: dict[str, Any], fn: Callable[[], Any]) -> Any:
        """Run one graph node with backend access to state; commit file writes at the boundary."""
        ctx = RunContext(state)
        token = run_context.set(ctx)
        try:
            out = fn()
        finally:
            run_context.reset(token)
        apply_update(state, {"files": ctx.pending_files} if ctx.pending_files else None)
        return out

    def invoke(self, state_in: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = {"messages": [], **copy.deepcopy(state_in)}
        for m in self.middleware:
            apply_update(state, self._node(state, lambda m=m: m.before_agent(state)))

        for _ in range(self.max_steps):
            request = ModelRequest(self.model, self.system_prompt, list(state["messages"]), list(self.tools), state)
            response: ModelResponse = self._node(state, lambda req=request: self._model_chain(req))
            apply_update(state, {**response.state_update, "messages": [response.message]})
            self.tracer("model_result", agent=self.name, state=state, update=response.state_update, message=response.message)
            calls = response.message.tool_calls
            if not calls:
                return state
            for call in calls:
                self.tracer("tool_call", agent=self.name, tool=call.name, args=call.args)
                req = ToolCallRequest(call, self._tools_by_name.get(call.name), state)
                result = self._node(state, lambda r=req: self._tool_chain(r))
                self.tracer("tool_result", agent=self.name, tool=call.name, result=result)
                if isinstance(result, Command):
                    apply_update(state, result.update)
                else:
                    apply_update(state, {"messages": [result]})
        msg = f"{self.name}: exceeded max_steps={self.max_steps}"
        raise RuntimeError(msg)


def final_text(state: dict[str, Any]) -> str:
    """Last non-empty AI text (deepagents walks back past empty trailing AIMessages)."""
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and m.content.strip():
            return m.content.strip()
    return ""
