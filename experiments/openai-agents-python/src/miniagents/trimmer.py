"""An opt-in `call_model_input_filter` that trims bulky tool outputs from older turns.

Upstream: `ToolOutputTrimmer` (`src/agents/extensions/tool_output_trimmer.py`, commit `bc9dbd7d`).
The runner itself never truncates tool outputs: they are replayed verbatim until something
like this filter, `SessionSettings.limit`, a session callback, or server-side compaction
intervenes. The filter only changes the per-call model view, never stored history.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .run import ModelInputData


@dataclass
class ToolOutputTrimmer:
    recent_turns: int = 2
    max_output_chars: int = 500
    preview_chars: int = 200

    def __call__(self, data: ModelInputData) -> ModelInputData:
        items = data.input
        user_indexes = [n for n, i in enumerate(items) if i.get("role") == "user" and i.get("type") in (None, "message")]
        if len(user_indexes) <= self.recent_turns:
            return data
        boundary = user_indexes[-self.recent_turns]  # Items from here on stay untouched.
        names = {i["call_id"]: i["name"] for i in items if i.get("type") == "function_call"}
        trimmed = []
        for n, item in enumerate(items):
            output = item.get("output")
            if n < boundary and item.get("type") == "function_call_output" and isinstance(output, str) and len(output) > self.max_output_chars:
                name = names.get(item["call_id"], "tool")
                preview = output[: self.preview_chars]
                item = {**item, "output": f"[Trimmed: {name} output — {len(output)} chars → {len(preview)} char preview]\n{preview}"}
            trimmed.append(item)
        return replace(data, input=trimmed)
