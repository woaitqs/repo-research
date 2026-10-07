import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mini_openhands import Agent, Conversation, ScriptedLLM, ToolSpec  # noqa: E402


@pytest.fixture
def make_conversation(tmp_path):
    """Build a conversation over a temp workspace + persistence dir."""

    def _make(script, *, condenser=None, conversation_id="conv-1", **kwargs):
        llm = ScriptedLLM(script)
        agent = Agent(
            llm=llm,
            tools=[ToolSpec("terminal"), ToolSpec("file_editor")],
            condenser=condenser,
        )
        conv = Conversation(
            agent=agent,
            workspace=tmp_path / "workspace",
            persistence_dir=tmp_path / "conversations",
            conversation_id=conversation_id,
            **kwargs,
        )
        return conv, llm

    return _make
