"""Thread-scoped sandbox. Lazy acquire, shared with sub-agents by id."""

from __future__ import annotations

from typing import Any

from minideer.messages import Message
from minideer.middleware import Middleware, ToolCall, ToolHandler


class Sandbox:
    def __init__(self, sandbox_id: str):
        self.id = sandbox_id
        self.files: dict[str, str] = {}

    def write(self, path: str, content: str) -> None:
        self.files[path] = content

    def read(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class SandboxProvider:
    def __init__(self) -> None:
        self._boxes: dict[str, Sandbox] = {}
        self._by_thread: dict[str, str] = {}

    def acquire(self, thread_id: str) -> str:
        if thread_id not in self._by_thread:
            sandbox_id = f"sbx-{thread_id}"
            self._by_thread[thread_id] = sandbox_id
            self._boxes[sandbox_id] = Sandbox(sandbox_id)
        return self._by_thread[thread_id]

    def get(self, sandbox_id: str) -> Sandbox:
        return self._boxes[sandbox_id]


class SandboxMiddleware(Middleware):
    """Acquire on the first tool call. The lead owns the skill projection.

    Upstream: SandboxMiddleware docstring (lazy_init) and
    owns_agent_skill_projection. Sub-agents share the lead sandbox and must
    not rebuild the skill view.
    """

    def __init__(self, provider: SandboxProvider, *, owns_projection: bool, skills: set[str] | None):
        self.provider = provider
        self.owns_projection = owns_projection
        self.skills = set(skills) if skills is not None else None

    def before_agent(self, state: dict, ctx: object) -> None:
        return None  # LAZY_ACQUIRE

    def wrap_tool_call(self, request: ToolCall, handler: ToolHandler) -> Message:
        state = request.state
        box = state.get("sandbox")
        if box is None or not box.get("sandbox_id"):
            sandbox_id = self.provider.acquire(request.ctx.thread_id)
            box = {"sandbox_id": sandbox_id}
            state["sandbox"] = box
        if self.owns_projection and "skill_view" not in box:
            box["skill_view"] = sorted(self.skills) if self.skills is not None else ["*"]
        return handler(request)


def sandbox_of(state: dict[str, Any], provider: SandboxProvider) -> Sandbox | None:
    box = state.get("sandbox") or {}
    sandbox_id = box.get("sandbox_id")
    if not sandbox_id:
        return None
    return provider.get(sandbox_id)
