"""The run protocol between backend and client (fake-headless-backend.ts, provider-turn-executor.ts)."""

import pytest

from conftest import approval, call, text, user
from minilc.backend import ActiveRunError, TURN_DID_NOT_COMPLETE
from minilc.model import ContextOverflowError, TransientProviderError


def kinds(chunks):
    return [c["message_type"] for c in chunks]


def stop(chunks):
    return [c["stop_reason"] for c in chunks if c["message_type"] == "stop_reason"][-1]


def test_tool_call_ends_the_run_with_requires_approval(make_backend):
    backend, model, _, conv = make_backend([call("c1", "Read", path="a.txt"), text("done")])
    first = list(backend.stream(conv, user("read a")))
    assert stop(first) == "requires_approval"
    assert "approval_request_message" in kinds(first)
    assert len(model.requests) == 1  # one model step per run, tool not executed here
    second = list(backend.stream(conv, approval("c1", "contents")))
    assert stop(second) == "end_turn"
    assert [m["role"] for m in model.requests[1]["messages"]] == ["user", "assistant", "toolResult"]


def test_denied_approval_becomes_error_tool_result(make_backend):
    backend, model, _, conv = make_backend([call("c1", "Bash", command="rm -rf /"), text("ok")])
    list(backend.stream(conv, user("clean up")))
    list(backend.stream(conv, {"messages": [{"type": "approval", "approvals": [
        {"type": "approval", "tool_call_id": "c1", "approve": False, "reason": "denied by policy"}]}]}))
    result = model.requests[1]["messages"][-1]
    assert result["role"] == "toolResult" and result["is_error"] and result["content"] == "denied by policy"


def test_dangling_tool_call_is_settled_before_next_user_turn(make_backend):
    backend, model, _, conv = make_backend([call("c1", "Bash", command="sleep 100"), text("ok")])
    list(backend.stream(conv, user("start")))
    list(backend.stream(conv, user("never mind")))  # no approval sent: interrupted
    roles = [m["role"] for m in model.requests[1]["messages"]]
    assert roles == ["user", "assistant", "toolResult", "user"]
    assert model.requests[1]["messages"][2]["content"] == TURN_DID_NOT_COMPLETE


def test_only_one_active_run_per_conversation(make_backend):
    backend, _, _, conv = make_backend([text("a"), text("b")])
    running = backend.stream(conv, user("one"))
    next(running)  # run started, not finished
    with pytest.raises(ActiveRunError):
        backend.stream(conv, user("two"))
    list(running)
    list(backend.stream(conv, user("two")))  # free again after completion


def test_transient_errors_retry_with_visible_events_then_give_up(make_backend):
    err = TransientProviderError("503")
    backend, model, _, conv = make_backend([err, err, text("recovered")])
    chunks = list(backend.stream(conv, user("hi")))
    assert [c["event_data"]["attempt"] for c in chunks if c.get("event_type") == "retry"] == [1, 2]
    assert stop(chunks) == "end_turn"
    backend2, _, _, conv2 = make_backend([err] * 5)
    assert stop(list(backend2.stream(conv2, user("hi")))) == "error"


def test_context_overflow_triggers_compaction_and_retry(make_backend):
    script = [text("a"), text("b"), text("c"), ContextOverflowError("too long"), text("SUMMARY"), text("fits now")]
    backend, model, _, conv = make_backend(script)
    for prompt in ("1", "2", "3"):
        list(backend.stream(conv, user(prompt)))
    chunks = list(backend.stream(conv, user("4")))
    assert ("event_message", "context_window_overflow") in [(c["message_type"], c.get("event_data", {}).get("trigger")) for c in chunks]
    assert stop(chunks) == "end_turn"
    assert model.requests[-1]["messages"][0]["role"] == "user"
    assert "system_alert" in model.requests[-1]["messages"][0]["content"]
