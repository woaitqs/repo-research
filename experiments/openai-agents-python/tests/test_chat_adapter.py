"""Model boundary: Responses-style items <-> Chat Completions wire format."""

import json

from miniagents import Agent, ChatCompletionsModel, ScriptedModel, function_tool, handoff
from miniagents.model import chat_completion_to_items, items_to_messages


def test_items_to_messages_merges_parallel_calls_and_maps_outputs():
    items = [
        {"role": "user", "content": "hi"},
        {"type": "function_call", "name": "a", "arguments": "{}", "call_id": "1"},
        {"type": "function_call", "name": "b", "arguments": "{}", "call_id": "2"},
        {"type": "function_call_output", "call_id": "1", "output": "A"},
        {"type": "function_call_output", "call_id": "2", "output": "B"},
        {"type": "message", "role": "assistant", "content": "done"},
    ]
    messages = items_to_messages("sys", items)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "tool", "assistant"]
    assert [c["id"] for c in messages[2]["tool_calls"]] == ["1", "2"]


def test_handoffs_become_plain_function_tools_on_the_wire():
    @function_tool
    def lookup(q: str) -> str:
        """Lookup."""
        return q

    billing = Agent(name="Billing", handoff_description="refunds", model=ScriptedModel([]))
    model = ChatCompletionsModel("m", base_url="http://unused", api_key="x")
    body = model.build_request(
        system_instructions="s",
        input=[{"role": "user", "content": "hi"}],
        tools=[lookup],
        handoffs=[handoff(billing)],
        settings={},
    )
    names = [t["function"]["name"] for t in body["tools"]]
    assert names == ["lookup", "transfer_to_billing"]
    assert body["messages"][0] == {"role": "system", "content": "s"}


def test_chat_completion_reply_becomes_items():
    payload = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [{"id": "c1", "function": {"name": "lookup", "arguments": json.dumps({"q": "x"})}}],
                }
            }
        ]
    }
    assert chat_completion_to_items(payload) == [
        {"type": "function_call", "name": "lookup", "arguments": '{"q": "x"}', "call_id": "c1"}
    ]
