"""MemFS compilation and compaction (system-prompt-compilation.ts, local-backend.ts, compaction.ts)."""

import json
import os

from conftest import call, text, user
from minilc import compaction, memfs


def write_memory(agent, rel, body, do_commit=True):
    with open(os.path.join(agent["memory_dir"], rel), "w", encoding="utf-8") as fh:
        fh.write(body)
    if do_commit:
        memfs.commit(agent["memory_dir"], f"update {rel}")


def visible(request, needle):
    return needle in request["system"] or needle in json.dumps(request["messages"])


def test_prompt_renders_core_memory_and_defers_child_directories(make_backend):
    backend, model, _, conv = make_backend([text("ok")])
    list(backend.stream(conv, user("hi")))
    system = model.requests[0]["system"]
    assert "<human>" in system and "Prefers short answers." in system
    assert '<directory path="projects/" index="projects/MEMORY.md" />' in system
    assert "DEEP_DETAIL_NOT_IN_PROMPT" not in system  # progressive disclosure


def test_uncommitted_memory_edit_is_invisible(make_backend):
    backend, model, agent, conv = make_backend([text("a"), text("b")])
    list(backend.stream(conv, user("hi")))
    write_memory(agent, "human.md", "---\nname: human\ndescription: u\n---\nUNCOMMITTED\n", do_commit=False)
    list(backend.stream(conv, user("again")))
    assert not visible(model.requests[1], "UNCOMMITTED")
    # a fresh compile (new conversation) still reads HEAD, not the working tree
    model.script.append(text("c"))
    fresh = backend.create_conversation(agent["id"])
    list(backend.stream(fresh, user("hello")))
    assert not visible(model.requests[2], "UNCOMMITTED")
    assert "Prefers short answers." in model.requests[2]["system"]


def test_committed_edit_arrives_once_as_delta_and_prompt_bytes_stay_stable(make_backend):
    backend, model, agent, conv = make_backend([text("a"), call("c1", "Bash", command="x"), text("b"), text("c")])
    list(backend.stream(conv, user("hi")))
    write_memory(agent, "human.md", "---\nname: human\ndescription: u\n---\nLIKES_TEAL\n")
    list(backend.stream(conv, user("turn A")))
    list(backend.stream(conv, {"messages": [{"type": "approval", "approvals": [
        {"type": "tool", "tool_call_id": "c1", "tool_return": "done"}]}]}))
    list(backend.stream(conv, user("turn B")))
    first, second, third = model.requests[1], model.requests[2], model.requests[3]
    assert first["messages"][-1]["role"] == "system" and "<memory_update>" in first["messages"][-1]["content"]
    assert first["system"] == model.requests[0]["system"]  # cache-stable prefix
    # Upstream behaviour reproduced (probe P3b): later calls no longer see it.
    assert not visible(second, "LIKES_TEAL") and not visible(third, "LIKES_TEAL")


def test_new_conversation_and_compaction_pick_up_committed_memory(make_backend):
    backend, model, agent, conv = make_backend([text("a"), text("b"), text("c"), text("d"),
                                                text("SUMMARY"), text("after"), text("fresh")])
    for prompt in ("1", "2", "3"):
        list(backend.stream(conv, user(prompt)))
    write_memory(agent, "human.md", "---\nname: human\ndescription: u\n---\nLIKES_TEAL\n")
    list(backend.stream(conv, user("4")))  # consumes the one-shot delta
    backend.compact(conv, mode="all")
    list(backend.stream(conv, user("5")))
    assert "LIKES_TEAL" in model.requests[-1]["system"]  # re-rendered by compaction
    fresh = backend.create_conversation(agent["id"])
    list(backend.stream(fresh, user("hello")))
    assert "LIKES_TEAL" in model.requests[-1]["system"]


def test_compaction_is_append_only_and_shrinks_only_the_view(make_backend):
    backend, model, _, conv = make_backend([text(str(i)) for i in range(6)] + [text("SUMMARY")])
    for i in range(6):
        list(backend.stream(conv, user(f"message {i}")))
    transcript = backend.transcript(conv)
    rows_before = transcript.rows()
    view_before = len(transcript.view())
    result = backend.compact(conv, mode="sliding_window")
    rows_after = transcript.rows()
    assert rows_after[: len(rows_before)] == rows_before and rows_after[-1]["type"] == "compaction"
    assert len(transcript.view()) == result["num_messages_after"] < view_before
    assert len(transcript.all_messages()) == 12  # full history kept (recall)
    head = transcript.view()[0]
    assert head["role"] == "user" and json.loads(head["content"])["type"] == "system_alert"


def test_sliding_window_never_separates_a_tool_call_from_its_result():
    msgs = []
    for i in range(10):
        msgs.append({"id": f"u{i}", "role": "user", "content": "x" * 400})
        msgs.append({"id": f"a{i}", "role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "name": "T", "arguments": {}}]})
        msgs.append({"id": f"r{i}", "role": "toolResult", "content": "y" * 400, "tool_call_id": f"c{i}"})
    evicted, kept = compaction.plan_sliding_window(msgs, context_window=2_000)
    assert kept[0]["role"] == "assistant"  # cut lands on an assistant message
    kept_calls = {c["id"] for m in kept if m["role"] == "assistant" for c in m["tool_calls"]}
    kept_results = {m["tool_call_id"] for m in kept if m["role"] == "toolResult"}
    assert kept_results <= kept_calls | {msgs[-1]["tool_call_id"]}
    assert evicted and len(evicted) + len(kept) == len(msgs)


def test_threshold_matches_upstream_numbers():
    assert compaction.compaction_threshold(200_000) == 183_616
    assert compaction.compaction_threshold(32_768) == 26_215
    assert not compaction.should_compact(180_000, 200_000) and compaction.should_compact(185_000, 200_000)


def test_sliding_window_goal_ignores_the_fixed_prompt_floor():
    """Upstream (compaction.ts:602-630) sizes the kept tail against (1 - 30%) x window using
    message tokens only. The system prompt + tool schemas (the floor) are not counted, so on a
    small window the compacted request can still exceed the window (seen with a real model:
    37k-token requests against a 40-45k window ending in max_tokens_exceeded)."""
    window, floor_tokens = 40_000, 24_000          # ~24k = letta-code's prompt + 19 tool schemas
    msgs = []
    for i in range(8):
        msgs.append({"id": f"u{i}", "role": "user", "content": "q" * 400})
        msgs.append({"id": f"a{i}", "role": "assistant", "content": "a" * 400, "tool_calls": []})
        msgs.append({"id": f"t{i}", "role": "user", "content": "r" * 12_000})
    _, kept = compaction.plan_sliding_window(msgs, context_window=window)
    kept_tokens = compaction.estimate_tokens(kept)
    assert kept_tokens < 0.7 * window                                  # the planner's own goal is met
    assert floor_tokens + kept_tokens > compaction.compaction_threshold(window)  # the real request is not
