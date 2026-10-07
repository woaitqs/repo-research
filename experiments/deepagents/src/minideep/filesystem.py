"""FilesystemMiddleware: the agent's "hands" plus the first context-engineering layer.

Mirrors deepagents/middleware/filesystem.py::FilesystemMiddleware:

1. Contributes `ls`, `read_file`, `write_file`, `edit_file`, `grep`, `execute`.
2. `wrap_model_call`: hides `execute` per request when the backend has no shell
   (`_filter_unsupported_tools_and_apply_prompt`) — the model never sees a tool that
   would always fail.
3. `wrap_tool_call`: rejects a second mutation of the same path in one model response
   (`_parallel_file_mutation_error`), then *offloads* any oversized tool result to
   `/large_tool_results/<tool_call_id>` and replaces it with a head/tail preview plus a
   pointer (`_intercept_large_tool_result`). The model can page the full result back
   in with `read_file(offset, limit)`.
"""

from __future__ import annotations

from minideep.backends import BackendProtocol, StateBackend, artifacts_root, supports_execution
from minideep.messages import AIMessage, Command, ToolMessage
from minideep.middleware import Middleware, ModelHandler, ModelRequest, ModelResponse, ToolCallRequest, ToolHandler
from minideep.tools import Tool

NUM_CHARS_PER_TOKEN = 4
# Same exclusion list as upstream TOOLS_EXCLUDED_FROM_EVICTION: these tools bound their
# own output (or re-reading an offloaded read_file result would not help).
TOOLS_EXCLUDED_FROM_EVICTION = frozenset({"ls", "glob", "grep", "read_file", "edit_file", "write_file", "delete"})
MUTATION_TOOLS = frozenset({"write_file", "edit_file"})


def _numbered(lines: list[str], start: int) -> str:
    return "\n".join(f"{i:6d}\t{line}" for i, line in enumerate(lines, start))


def preview(content: str, head: int = 5, tail: int = 5) -> str:
    """Head + tail preview with a truncation marker (upstream `_create_content_preview`)."""
    lines = content.splitlines()
    if len(lines) <= head + tail:
        return _numbered(lines, 1)
    marker = f"... [{len(lines) - head - tail} lines truncated] ..."
    return f"{_numbered(lines[:head], 1)}\n{marker}\n{_numbered(lines[-tail:], len(lines) - tail + 1)}"


class FilesystemMiddleware(Middleware):
    def __init__(self, backend: BackendProtocol | None = None, *, tool_token_limit_before_evict: int | None = 20_000) -> None:
        self.backend = backend or StateBackend()
        self.limit_chars = tool_token_limit_before_evict * NUM_CHARS_PER_TOKEN if tool_token_limit_before_evict else None
        self.large_results_prefix = f"{artifacts_root(self.backend)}/large_tool_results"
        self.tools = self._make_tools()

    # -- tools ----------------------------------------------------------------
    def _make_tools(self) -> list[Tool]:
        b = self.backend

        def ls(path: str = "/") -> str:
            return "\n".join(b.ls(path)) or "(empty)"

        def read_file(file_path: str, offset: int = 0, limit: int = 100) -> str:
            lines = b.read(file_path).splitlines()
            window = lines[offset : offset + limit]
            if not window:
                return f"Error: offset {offset} is past the end of {file_path} ({len(lines)} lines)"
            more = len(lines) - (offset + len(window))
            note = f"\n[{more} more lines; call read_file with offset={offset + len(window)}]" if more > 0 else ""
            return _numbered(window, offset + 1) + note

        def write_file(file_path: str, content: str) -> str:
            b.write(file_path, content)
            return f"Wrote {len(content)} chars to {file_path}"

        def edit_file(file_path: str, old_string: str, new_string: str) -> str:
            b.edit(file_path, old_string, new_string)
            return f"Edited {file_path}"

        def grep(pattern: str, path: str = "/") -> str:
            hits = b.grep(pattern, path)
            return "\n".join(f"{p}:{n}: {line}" for p, n, line in hits[:200]) or "No matches"

        def execute(command: str) -> str:
            output, code = b.execute(command)  # type: ignore[attr-defined]
            return f"{output}\n[exit code: {code}]"

        fns = (ls, read_file, write_file, edit_file, grep, execute)
        docs = {
            "ls": "List files under a directory.",
            "read_file": "Read a file with line numbers. Paginate with offset/limit.",
            "write_file": "Create or overwrite a file.",
            "edit_file": "Replace one exact occurrence of old_string with new_string.",
            "grep": "Search for a literal string in files.",
            "execute": "Run a shell command in the backend's environment.",
        }
        return [Tool(name=f.__name__, description=docs[f.__name__], func=f) for f in fns]

    # -- hooks ----------------------------------------------------------------
    def wrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        if not supports_execution(self.backend):
            request = request.override(tools=[t for t in request.tools if t.name != "execute"])
        return handler(request)

    def wrap_tool_call(self, request: ToolCallRequest, handler: ToolHandler) -> ToolMessage | Command:
        if error := self._parallel_mutation_error(request):
            return error
        result = handler(request)
        if isinstance(result, ToolMessage) and request.call.name not in TOOLS_EXCLUDED_FROM_EVICTION:
            return self._maybe_offload(result)
        return result

    def _parallel_mutation_error(self, request: ToolCallRequest) -> ToolMessage | None:
        call = request.call
        if call.name not in MUTATION_TOOLS:
            return None
        last_ai = next((m for m in reversed(request.state["messages"]) if isinstance(m, AIMessage)), None)
        for other in last_ai.tool_calls if last_ai else []:
            if other.id == call.id:
                return None  # only *later* calls to the same path are rejected
            if other.name in MUTATION_TOOLS and other.args.get("file_path") == call.args.get("file_path"):
                return ToolMessage(
                    "Error: parallel file mutations to the same path are not allowed.",
                    tool_call_id=call.id,
                    name=call.name,
                    status="error",
                )
        return None

    def _maybe_offload(self, msg: ToolMessage) -> ToolMessage:
        if self.limit_chars is None or len(msg.content) <= self.limit_chars:
            return msg
        path = f"{self.large_results_prefix}/{msg.tool_call_id}"
        try:
            self.backend.write(path, msg.content)
        except Exception:  # noqa: BLE001 - offload failure keeps the original result
            return msg
        stub = (
            f"Tool result too large, the result of this tool call {msg.tool_call_id} was saved in the "
            f"filesystem at this path: {path}\n\nYou can read the result with read_file, a part at a time "
            f"(offset and limit).\n\nHere is a preview showing the head and tail of the result:\n\n{preview(msg.content)}"
        )
        return ToolMessage(stub, id=msg.id, tool_call_id=msg.tool_call_id, name=msg.name, status=msg.status, meta={"offloaded_to": path})
