import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from minilc.backend import LocalBackend  # noqa: E402
from minilc.model import ScriptedModel  # noqa: E402

MEMORY = {
    "MEMORY.md": "# Memory\n- [projects](projects/MEMORY.md)\n",
    "human.md": "---\nname: human\ndescription: About the user\n---\nPrefers short answers.\n",
    "persona.md": "---\nname: persona\ndescription: Who I am\n---\nA careful coding agent.\n",
    "projects/MEMORY.md": "# Projects\n",
    "projects/alpha.md": "---\nname: alpha\ndescription: Project alpha\n---\nDEEP_DETAIL_NOT_IN_PROMPT\n",
}


def text(t):
    return {"text": t}


def call(cid, name, **args):
    return {"text": "", "tool_calls": [{"id": cid, "name": name, "arguments": args}]}


@pytest.fixture
def make_backend(tmp_path):
    def _make(script, **kw):
        model = ScriptedModel(script, context_window=kw.pop("context_window", 200_000))
        backend = LocalBackend(str(tmp_path / "store"), model, **kw)
        agent = backend.create_agent("probe", "BASE PROMPT\n{CORE_MEMORY}", dict(MEMORY))
        conv = backend.create_conversation(agent["id"])
        return backend, model, agent, conv
    return _make


def user(content):
    return {"messages": [{"role": "user", "content": content}]}


def approval(cid, ret):
    return {"messages": [{"type": "approval", "approvals": [
        {"type": "tool", "tool_call_id": cid, "tool_return": ret, "status": "success"}]}]}
