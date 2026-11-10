"""The owned continuation: step, auto-once, or a capped ReAct loop."""

from __future__ import annotations

from typing import Any, Callable

from minispace.loop import PiStep, RUN_LAST_MESSAGE_ERROR, is_runnable
from minispace.model import ScriptedModel
from minispace.render import render_thread
from minispace.tools import Tool, needs_human

MAX_AUTO_TOOL_TURNS = 50

Executor = Callable[[Tool, dict[str, Any], str], str]


class Workbench:
    def __init__(
        self,
        model: ScriptedModel,
        tools: list[Tool],
        executors: dict[str, Executor] | None = None,
    ):
        self.model = model
        self.tools = {tool.name: tool for tool in tools}
        self.executors = executors or {}
        self.pi = PiStep(model)

    def run(
        self,
        thread: dict[str, Any],
        *,
        mode: str = "step",
        runtime: str = "local",
        values: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if mode not in ("step", "auto", "react"):
            raise ValueError(mode)
        messages = list(thread.get("messages") or [])
        if not is_runnable(messages):
            raise RuntimeError(RUN_LAST_MESSAGE_ERROR)

        values = dict(thread.get("values") or {}) if values is None else dict(values)
        snapshot = dict(thread.get("snapshot") or {})
        tool_executions: list[dict[str, Any]] = []
        stop = "model_done"
        failed = False
        saw_model = False

        # REACT_CAP: at most this many model turns, including a step-mode run.
        for _turn in range(MAX_AUTO_TOOL_TURNS):
            request, snapshot = render_thread(
                {**thread, "messages": messages, "snapshot": snapshot},
                values,
            )
            try:
                message = self.pi.step(request)
            except Exception:
                failed = True
                stop = "failed"
                break
            saw_model = True
            messages = [*messages, message]
            calls = message.get("tool_calls") or []
            if not calls:
                stop = "model_done"
                break
            if mode == "step":
                stop = "step"
                break
            filled, reason, executions = self._fill_tools(calls, runtime)
            tool_executions.extend(executions)
            if filled is None:
                stop = reason
                break
            messages = [*messages[:-1], {**message, "tool_calls": filled}]
            if mode == "auto":
                stop = "auto_once"
                break
        else:
            stop = "cap"

        run_record = None
        if saw_model and not failed:
            run_record = {
                "id": f"run_{self.model and len(self.model.requests)}",
                "thread": {
                    "system": thread.get("system", ""),
                    "messages": messages,
                    "snapshot": snapshot,
                },
            }

        return {
            "messages": messages,
            "snapshot": snapshot,
            "stored_system": thread.get("system", ""),
            "model_calls": len(self.model.requests),
            "tool_executions": tool_executions,
            "pi_executed": list(self.pi.executed),
            "stop": stop,
            "run_record": run_record,
            "failed": failed,
        }

    def _fill_tools(
        self,
        calls: list[dict[str, Any]],
        runtime: str,
    ) -> tuple[list[dict[str, Any]] | None, str, list[dict[str, Any]]]:
        planned: list[tuple[dict[str, Any], Tool]] = []
        for call in calls:
            tool = self.tools.get(call["name"])
            if tool is None or needs_human(tool, call.get("arguments") or {}):
                return None, "human", []  # HUMAN_FUNCTION
            planned.append((call, tool))

        executions: list[dict[str, Any]] = []
        filled: list[dict[str, Any]] = []
        for call, tool in planned:
            try:
                if tool.kind == "plugin" and runtime != "local":
                    raise RuntimeError(
                        "Plugin Tools are available only in the local runtime."
                    )
                executor = self.executors.get(tool.name)
                if executor is None:
                    raise RuntimeError(f"No executor for {tool.name}")
                text = executor(tool, call.get("arguments") or {}, runtime)
                output, is_error = text, False
            except Exception as error:
                output, is_error = str(error), True
            executions.append(
                {"name": tool.name, "kind": tool.kind, "is_error": is_error}
            )
            filled.append({**call, "output": output, "is_error": is_error})
        return filled, "ok", executions
