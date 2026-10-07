"""Runtime probe of the real openai-agents SDK (no network).

Each probe drives `Runner.run` with the SDK's own `agents.testing.ScriptedModel`, records what
the model boundary actually received, and compares it with what the source code predicts.

Run from a checkout of openai/openai-agents-python at the pinned commit:

    uv run --frozen python /path/to/probe_openai_agents.py [output.json]

The probe never calls a real model. Sandbox probes use `UnixLocalSandboxClient`, which runs
commands in a temporary local workspace.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from agents import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelSettings,
    RunConfig,
    Runner,
    RunState,
    SQLiteSession,
    function_tool,
    input_guardrail,
)
from agents.extensions import ToolOutputTrimmer
from agents.memory import SessionSettings
from agents.testing import ScriptedModel, assistant_message, function_call

RESULTS: list[dict[str, Any]] = []


def _items(call_input: Any) -> list[dict[str, Any]]:
    """Normalize a recorded model input into plain dicts for inspection."""
    if isinstance(call_input, str):
        return [{"role": "user", "content": call_input}]
    out = []
    for item in call_input:
        if isinstance(item, dict):
            out.append(item)
        elif hasattr(item, "model_dump"):
            out.append(item.model_dump(exclude_unset=True))
        else:
            out.append({"repr": repr(item)})
    return out


def _kinds(call_input: Any) -> list[str]:
    kinds = []
    for item in _items(call_input):
        kinds.append(item.get("type") or f"message:{item.get('role')}")
    return kinds


def _text(call_input: Any) -> str:
    return json.dumps(_items(call_input), ensure_ascii=False, default=str)


def record(pid: str, claim: str, expected: str, observed: Any, ok: bool) -> None:
    RESULTS.append(
        {"id": pid, "claim": claim, "expected": expected, "observed": observed, "pass": ok}
    )
    print(f"[{'PASS' if ok else 'FAIL'}] {pid}: {claim}")


async def probe_handoff_is_a_tool_and_shares_history() -> None:
    billing_model = ScriptedModel([[assistant_message("billing handled")]])
    billing = Agent(
        name="Billing",
        instructions="You are billing.",
        handoff_description="Handles refunds and charges.",
        model=billing_model,
    )
    triage_model = ScriptedModel([[function_call("transfer_to_billing", {}, call_id="h1")]])
    triage = Agent(name="Triage", instructions="Route.", model=triage_model, handoffs=[billing])
    result = await Runner.run(triage, "I was double charged", run_config=RunConfig(tracing_disabled=True))

    first = triage_model.first_call
    handoff_names = [h.tool_name for h in first.handoffs]
    child = billing_model.first_call
    kinds = _kinds(child.input)
    ok = (
        handoff_names == ["transfer_to_billing"]
        and first.tools == []
        and child.system_instructions == "You are billing."
        and kinds == ["message:user", "function_call", "function_call_output"]
        and result.last_agent.name == "Billing"
    )
    record(
        "P1",
        "A handoff is a tool call; by default the next agent sees the full raw transcript",
        "handoffs=[transfer_to_billing]; Billing input = [user, function_call, function_call_output]",
        {
            "triage_handoffs": handoff_names,
            "triage_function_tools": [t.name for t in first.tools],
            "billing_system": child.system_instructions,
            "billing_input_kinds": kinds,
            "transfer_output": _items(child.input)[-1].get("output"),
            "last_agent": result.last_agent.name,
        },
        ok,
    )


async def probe_nested_handoff_history() -> None:
    billing_model = ScriptedModel([[assistant_message("ok")]])
    billing = Agent(name="Billing", instructions="b", model=billing_model)
    triage_model = ScriptedModel([[function_call("transfer_to_billing", {}, call_id="h1")]])
    triage = Agent(name="Triage", model=triage_model, handoffs=[billing])
    await Runner.run(
        triage,
        "refund please",
        run_config=RunConfig(tracing_disabled=True, nest_handoff_history=True),
    )
    items = _items(billing_model.first_call.input)
    text = _text(billing_model.first_call.input)
    ok = len(items) == 1 and "<CONVERSATION HISTORY>" in text and "function_call" not in _kinds(
        billing_model.first_call.input
    )
    record(
        "P2",
        "nest_handoff_history=True collapses the transcript into one summary message",
        "Billing input = 1 message wrapped in <CONVERSATION HISTORY>",
        {"billing_input_kinds": _kinds(billing_model.first_call.input), "preview": text[:300]},
        ok,
    )


async def probe_agent_as_tool_isolation() -> None:
    worker_model = ScriptedModel([[assistant_message("research result: 42")]])
    worker = Agent(name="Researcher", instructions="Research.", model=worker_model)
    orch_model = ScriptedModel(
        [
            [function_call("research", {"input": "find the answer"}, call_id="t1")],
            [assistant_message("The answer is 42.")],
        ]
    )
    orch = Agent(
        name="Orchestrator",
        model=orch_model,
        tools=[worker.as_tool("research", "Researches a question.")],
    )
    result = await Runner.run(
        orch, "PARENT-SECRET-XYZ: what is the answer?", run_config=RunConfig(tracing_disabled=True)
    )
    child_input = _items(worker_model.first_call.input)
    parent_second = _items(orch_model.calls[1].input)
    tool_output = [i for i in parent_second if i.get("type") == "function_call_output"]
    ok = (
        len(child_input) == 1
        and "PARENT-SECRET-XYZ" not in _text(worker_model.first_call.input)
        and tool_output
        and tool_output[0].get("output") == "research result: 42"
        and result.last_agent.name == "Orchestrator"
        and orch_model.first_call.tools[0].params_json_schema.get("required") == ["input"]
    )
    record(
        "P3",
        "Agent.as_tool runs a nested Runner with only the generated input; parent gets final text",
        "child input = [user('find the answer')], parent sees function_call_output 'research result: 42'",
        {
            "child_input": child_input,
            "parent_tool_output": tool_output[0].get("output") if tool_output else None,
            "tool_schema": orch_model.first_call.tools[0].params_json_schema,
            "last_agent": result.last_agent.name,
        },
        bool(ok),
    )


async def probe_tool_error_redacted() -> None:
    @function_tool
    def lookup(order_id: str) -> str:
        """Look up an order."""
        raise ValueError("db failure: password=hunter2")

    model = ScriptedModel(
        [
            [
                function_call("lookup", {"order_id": "A1"}, call_id="c1"),
                function_call("lookup", "{not json", call_id="c2"),
            ],
            [assistant_message("sorry")],
        ]
    )
    agent = Agent(name="A", model=model, tools=[lookup])
    result = await Runner.run(agent, "check order", run_config=RunConfig(tracing_disabled=True))
    outputs = [i for i in _items(model.calls[1].input) if i.get("type") == "function_call_output"]
    texts = [o.get("output") for o in outputs]
    ok = (
        len(texts) == 2
        and all(t == "An error occurred while running the tool. Please try again." for t in texts)
        and "hunter2" not in _text(model.calls[1].input)
        and result.final_output == "sorry"
    )
    record(
        "P4",
        "Tool exceptions and bad JSON become a fixed, redacted observation; the loop continues",
        "both outputs == default generic message, no exception text reaches the model",
        {"outputs": texts, "final_output": result.final_output},
        ok,
    )


async def probe_tool_not_found() -> None:
    model = ScriptedModel([[function_call("ghost", {}, call_id="g1")]])
    agent = Agent(name="A", model=model)
    try:
        await Runner.run(agent, "x", run_config=RunConfig(tracing_disabled=True))
        default_behavior = "no error"
    except ModelBehaviorError as e:
        default_behavior = f"ModelBehaviorError: {e}"

    model2 = ScriptedModel([[function_call("ghost", {}, call_id="g1")], [assistant_message("ok")]])
    agent2 = Agent(name="A", model=model2)
    await Runner.run(
        agent2,
        "x",
        run_config=RunConfig(tracing_disabled=True, tool_not_found_behavior="return_error_to_model"),
    )
    outs = [i for i in _items(model2.calls[1].input) if i.get("type") == "function_call_output"]
    ok = default_behavior.startswith("ModelBehaviorError") and bool(outs)
    record(
        "P5",
        "Unknown tool raises by default; return_error_to_model turns it into an observation",
        "default -> ModelBehaviorError; opt-in -> function_call_output",
        {"default": default_behavior, "opt_in_output": outs[0].get("output") if outs else None},
        ok,
    )


async def probe_max_turns() -> None:
    @function_tool
    def ping() -> str:
        """Ping."""
        return "pong"

    model = ScriptedModel([[function_call("ping", {}, call_id=f"p{i}")] for i in range(5)])
    agent = Agent(name="A", model=model, tools=[ping])
    try:
        await Runner.run(agent, "loop", max_turns=2, run_config=RunConfig(tracing_disabled=True))
        outcome = "finished"
    except MaxTurnsExceeded as e:
        outcome = f"MaxTurnsExceeded: {e}"
    ok = outcome.startswith("MaxTurnsExceeded") and len(model.calls) == 2
    record(
        "P6",
        "max_turns bounds model invocations (a turn = one model call + its tool side effects)",
        "MaxTurnsExceeded after exactly 2 model calls",
        {"outcome": outcome, "model_calls": len(model.calls)},
        ok,
    )


async def probe_hitl_resume() -> None:
    executed: list[str] = []

    @function_tool(needs_approval=True)
    def delete_file(path: str) -> str:
        """Delete a file."""
        executed.append(path)
        return f"deleted {path}"

    model = ScriptedModel(
        [
            [function_call("delete_file", {"path": "/tmp/a"}, call_id="d1")],
            [assistant_message("done")],
        ]
    )
    agent = Agent(name="A", model=model, tools=[delete_file])
    first = await Runner.run(agent, "delete /tmp/a", run_config=RunConfig(tracing_disabled=True))
    interrupted = [i.raw_item.name for i in first.interruptions]
    executed_before = list(executed)
    payload = first.to_state().to_json()
    restored = await RunState.from_json(agent, json.loads(json.dumps(payload)))
    for item in restored.get_interruptions():
        restored.approve(item)
    second = await Runner.run(agent, restored, run_config=RunConfig(tracing_disabled=True))
    ok = (
        interrupted == ["delete_file"]
        and executed_before == []
        and executed == ["/tmp/a"]
        and second.final_output == "done"
        and len(model.calls) == 2
    )
    record(
        "P7",
        "needs_approval pauses before side effects; RunState JSON round-trip + approve resumes",
        "interruption, no execution, then exactly one execution and one more model call",
        {
            "interruptions": interrupted,
            "executed_before_approval": executed_before,
            "executed_after": executed,
            "schema_version": payload.get("$schemaVersion"),
            "state_keys": sorted(payload.keys())[:12],
            "model_calls_total": len(model.calls),
            "final_output": second.final_output,
        },
        ok,
    )


async def probe_session_history_and_limit() -> None:
    session = SQLiteSession("probe-session")
    model = ScriptedModel([[assistant_message("Nice to meet you, Ada.")], [assistant_message("Ada")]])
    agent = Agent(name="A", model=model)
    cfg = RunConfig(tracing_disabled=True)
    await Runner.run(agent, "My name is Ada.", session=session, run_config=cfg)
    await Runner.run(agent, "What is my name?", session=session, run_config=cfg)
    second_kinds = _kinds(model.calls[1].input)
    stored = await session.get_items()

    limited = SQLiteSession("probe-session-limit", session_settings=SessionSettings(limit=1))
    model2 = ScriptedModel([[assistant_message("a")], [assistant_message("b")]])
    agent2 = Agent(name="B", model=model2)
    await Runner.run(agent2, "first", session=limited, run_config=cfg)
    await Runner.run(agent2, "second", session=limited, run_config=cfg)
    limited_kinds = _kinds(model2.calls[1].input)
    ok = (
        second_kinds == ["message:user", "message", "message:user"]
        and len(stored) == 4
        and len(limited_kinds) == 2
    )
    record(
        "P8",
        "Session prepends stored history before each run; SessionSettings.limit keeps only the tail",
        "run 2 input = [user, assistant, user]; limit=1 keeps 1 history item + new input",
        {
            "run2_input_kinds": second_kinds,
            "stored_items": len(stored),
            "limit1_run2_input": _items(model2.calls[1].input),
        },
        ok,
    )


async def probe_tool_output_trimmer() -> None:
    big = "X" * 5000
    history = [
        {"role": "user", "content": "old question"},
        {"type": "function_call", "call_id": "old1", "name": "search", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "old1", "output": big},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
    ]
    model_plain = ScriptedModel([[assistant_message("a")]])
    await Runner.run(Agent(name="A", model=model_plain), list(history), run_config=RunConfig(tracing_disabled=True))
    model_trim = ScriptedModel([[assistant_message("a")]])
    await Runner.run(
        Agent(name="A", model=model_trim),
        list(history),
        run_config=RunConfig(
            tracing_disabled=True,
            call_model_input_filter=ToolOutputTrimmer(recent_turns=1, max_output_chars=500),
        ),
    )

    def _out_len(m: ScriptedModel) -> int:
        out = [i for i in _items(m.first_call.input) if i.get("type") == "function_call_output"][0]
        return len(json.dumps(out.get("output")))

    plain, trimmed = _out_len(model_plain), _out_len(model_trim)
    trimmed_out = [i for i in _items(model_trim.first_call.input) if i.get("type") == "function_call_output"][0]
    ok = plain > 5000 and trimmed < 600
    record(
        "P9",
        "Tool outputs are replayed verbatim by default; ToolOutputTrimmer (opt-in filter) trims old ones",
        "default keeps 5000 chars; trimmer replaces the old output with a short preview",
        {"default_len": plain, "trimmed_len": trimmed, "trimmed_preview": str(trimmed_out.get("output"))[:240]},
        ok,
    )


async def probe_stop_on_first_tool() -> None:
    @function_tool
    def weather(city: str) -> str:
        """Weather."""
        return f"{city}: sunny"

    model = ScriptedModel([[function_call("weather", {"city": "Paris"}, call_id="w1")]])
    agent = Agent(name="A", model=model, tools=[weather], tool_use_behavior="stop_on_first_tool")
    result = await Runner.run(agent, "weather?", run_config=RunConfig(tracing_disabled=True))
    ok = result.final_output == "Paris: sunny" and len(model.calls) == 1
    record(
        "P10",
        "tool_use_behavior='stop_on_first_tool' makes the tool output the final output",
        "final_output = tool output, model called once",
        {"final_output": result.final_output, "model_calls": len(model.calls)},
        ok,
    )


async def probe_input_guardrails() -> None:
    @input_guardrail(run_in_parallel=False)
    async def block_seq(ctx, agent, inp):
        return GuardrailFunctionOutput(output_info="blocked", tripwire_triggered=True)

    model = ScriptedModel([[assistant_message("should not run")]])
    try:
        await Runner.run(
            Agent(name="A", model=model, input_guardrails=[block_seq]),
            "hi",
            run_config=RunConfig(tracing_disabled=True),
        )
        seq = "no trip"
    except InputGuardrailTripwireTriggered:
        seq = "tripped"
    seq_calls = len(model.calls)

    @input_guardrail
    async def block_par(ctx, agent, inp):
        await asyncio.sleep(0.05)
        return GuardrailFunctionOutput(output_info="blocked", tripwire_triggered=True)

    model2 = ScriptedModel([[assistant_message("raced")]])
    try:
        await Runner.run(
            Agent(name="B", model=model2, input_guardrails=[block_par]),
            "hi",
            run_config=RunConfig(tracing_disabled=True),
        )
        par = "no trip"
    except InputGuardrailTripwireTriggered:
        par = "tripped"
    ok = seq == "tripped" and seq_calls == 0 and par == "tripped"
    record(
        "P11",
        "Sequential input guardrails block before the model call; parallel ones race the model",
        "sequential: 0 model calls; parallel: tripwire still raised",
        {
            "sequential": seq,
            "sequential_model_calls": seq_calls,
            "parallel": par,
            "parallel_model_calls": len(model2.calls),
        },
        ok,
    )


async def probe_reset_tool_choice_and_dynamic_instructions() -> None:
    calls = {"n": 0}

    def dyn(ctx, agent) -> str:
        calls["n"] += 1
        return f"instructions v{calls['n']}"

    @function_tool
    def ping() -> str:
        """Ping."""
        return "pong"

    model = ScriptedModel([[function_call("ping", {}, call_id="p1")], [assistant_message("done")]])
    agent = Agent(
        name="A",
        model=model,
        tools=[ping],
        instructions=dyn,
        model_settings=ModelSettings(tool_choice="required"),
    )
    await Runner.run(agent, "go", run_config=RunConfig(tracing_disabled=True))
    first_choice = model.calls[0].model_settings.tool_choice
    second_choice = model.calls[1].model_settings.tool_choice
    systems = [c.system_instructions for c in model.calls]
    ok = first_choice == "required" and second_choice in (None, "auto") and systems == [
        "instructions v1",
        "instructions v2",
    ]
    record(
        "P12",
        "Instructions are re-resolved every turn; tool_choice is reset after a tool call",
        "system prompts v1,v2; tool_choice required -> reset",
        {"systems": systems, "tool_choice_turn1": first_choice, "tool_choice_turn2": second_choice},
        ok,
    )


async def probe_parallel_tools() -> None:
    @function_tool
    async def slow(tag: str) -> str:
        """Slow tool."""
        await asyncio.sleep(0.4 if tag == "a" else 0.1)
        return f"done-{tag}"

    model = ScriptedModel(
        [
            [
                function_call("slow", {"tag": "a"}, call_id="s1"),
                function_call("slow", {"tag": "b"}, call_id="s2"),
            ],
            [assistant_message("ok")],
        ]
    )
    agent = Agent(name="A", model=model, tools=[slow])
    t0 = time.perf_counter()
    await Runner.run(agent, "go", run_config=RunConfig(tracing_disabled=True))
    elapsed = time.perf_counter() - t0
    outs = [i.get("output") for i in _items(model.calls[1].input) if i.get("type") == "function_call_output"]
    ok = elapsed < 0.48 and outs == ["done-a", "done-b"]
    record(
        "P13",
        "Tool calls from one response run concurrently; outputs keep model order",
        "wall time ~max(0.4,0.1) not sum; outputs [a, b] although b finishes first",
        {"elapsed_s": round(elapsed, 3), "outputs_in_order": outs},
        ok,
    )


async def probe_sandbox_agent() -> None:
    from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
    from agents.sandbox.capabilities import Compaction, Filesystem, Memory, Shell, Skill, Skills
    from agents.sandbox.config import MemoryReadConfig
    from agents.sandbox.entries import File
    from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient

    manifest = Manifest(
        entries={
            "notes.md": File(content=b"launch date: 2026-11-02\n"),
            "memories/memory_summary.md": File(content=b"- The user prefers terse answers.\n"),
        }
    )
    model = ScriptedModel(
        [
            [function_call("exec_command", {"cmd": "cat notes.md"}, call_id="e1")],
            [assistant_message("2026-11-02")],
        ]
    )
    agent = SandboxAgent(
        name="Analyst",
        instructions="Answer from the workspace.",
        model=model,
        default_manifest=manifest,
        capabilities=[
            Filesystem(),
            Shell(),
            Compaction(),
            Skills(
                skills=[
                    Skill(
                        name="release-notes",
                        description="Write release notes from a changelog.",
                        content="SECRET-SKILL-BODY step 1...",
                    )
                ]
            ),
            Memory(read=MemoryReadConfig(live_update=False), generate=None),
        ],
    )
    result = await Runner.run(
        agent,
        "When do we launch?",
        run_config=RunConfig(tracing_disabled=True, sandbox=SandboxRunConfig(client=UnixLocalSandboxClient())),
    )
    first = model.first_call
    system = first.system_instructions or ""
    tool_names = sorted(t.name for t in first.tools)
    extra = first.model_settings.extra_args or {}
    out = [i.get("output") for i in _items(model.calls[1].input) if i.get("type") == "function_call_output"]
    section_order = [
        system.find("# Agent instructions"),
        system.find("# Sandbox capability instructions"),
        system.find("# Filesystem"),
    ]
    ok = (
        result.final_output == "2026-11-02"
        and {"exec_command", "apply_patch", "view_image"} <= set(tool_names)
        and "release-notes: Write release notes" in system
        and "SECRET-SKILL-BODY" not in system
        and "The user prefers terse answers." in system
        and extra.get("context_management", [{}])[0].get("type") == "compaction"
        and out
        and "launch date: 2026-11-02" in out[0]
        and section_order == sorted(section_order)
        and -1 not in section_order
    )
    record(
        "P14",
        "SandboxAgent = Agent prepared per run by capabilities (tools, prompt fragments, sampling params)",
        "exec_command/apply_patch/view_image tools; skills index (no body) + memory summary in prompt; "
        "context_management compaction param; shell output reaches the model",
        {
            "tools": tool_names,
            "system_prompt_chars": len(system),
            "section_offsets": section_order,
            "skill_body_in_prompt": "SECRET-SKILL-BODY" in system,
            "memory_summary_in_prompt": "The user prefers terse answers." in system,
            "extra_args": extra,
            "exec_output_preview": (out[0] if out else "")[:160],
            "final_output": result.final_output,
            "public_last_agent_is_original": result.last_agent is agent,
        },
        bool(ok),
    )


async def probe_compaction_process_context() -> None:
    from agents.sandbox.capabilities import Compaction

    ctx = [
        {"role": "user", "content": "very old"},
        {"role": "assistant", "content": "old reply"},
        {"type": "compaction", "id": "cmp_1", "encrypted_content": "..."},
        {"role": "user", "content": "new"},
    ]
    out = Compaction().process_context(list(ctx))
    ok = [i.get("type") or i.get("role") for i in out] == ["compaction", "user"]
    record(
        "P15",
        "Compaction capability drops every item before the latest server compaction item",
        "[compaction, user]",
        {"kept": [i.get("type") or i.get("role") for i in out]},
        ok,
    )


async def probe_parallel_guardrail_side_effect_race() -> None:
    executed: list[str] = []

    @function_tool
    def send_email(to: str) -> str:
        """Send an email."""
        executed.append(to)
        return "sent"

    @input_guardrail
    async def slow_block(ctx, agent, inp):
        await asyncio.sleep(0.3)
        return GuardrailFunctionOutput(output_info="blocked", tripwire_triggered=True)

    async def run_once(streamed: bool) -> tuple[str, list[str]]:
        executed.clear()
        model = ScriptedModel(
            [[function_call("send_email", {"to": "a@b.c"}, call_id="e1")], [assistant_message("done")]]
        )
        agent = Agent(name="A", model=model, tools=[send_email], input_guardrails=[slow_block])
        cfg = RunConfig(tracing_disabled=True)
        try:
            if streamed:
                result = Runner.run_streamed(agent, "email them", run_config=cfg)
                async for _ in result.stream_events():
                    pass
            else:
                await Runner.run(agent, "email them", run_config=cfg)
            outcome = "no trip"
        except InputGuardrailTripwireTriggered:
            outcome = "tripped"
        return outcome, list(executed)

    non_streamed = await run_once(False)
    streamed = await run_once(True)
    ok = non_streamed[0] == "tripped" and streamed[0] == "tripped"
    record(
        "P16",
        "A slow parallel input guardrail can lose the race to tool side effects",
        "tripwire raised in both modes; observe whether send_email already ran",
        {
            "non_streamed": {"outcome": non_streamed[0], "executed": non_streamed[1]},
            "streamed": {"outcome": streamed[0], "executed": streamed[1]},
        },
        ok,
    )


async def probe_sandbox_capabilities_on_chat_completions() -> None:
    from openai import AsyncOpenAI

    from agents import OpenAIChatCompletionsModel
    from agents.sandbox import SandboxAgent, SandboxRunConfig
    from agents.sandbox.capabilities import Compaction, Shell
    from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient

    # Port 9 (discard) is closed: reaching it proves the request was well-formed for the client.
    client = AsyncOpenAI(base_url="http://127.0.0.1:9/v1", api_key="probe", max_retries=0)
    observed: dict[str, str] = {}
    for label, caps in (
        ("default (Filesystem, Shell, Compaction)", None),
        ("Shell + Compaction", [Shell(), Compaction()]),
        ("Shell only", [Shell()]),
    ):
        kwargs = {} if caps is None else {"capabilities": caps}
        agent = SandboxAgent(name="A", model=OpenAIChatCompletionsModel("m", client), **kwargs)
        try:
            await Runner.run(
                agent,
                "hi",
                run_config=RunConfig(
                    tracing_disabled=True, sandbox=SandboxRunConfig(client=UnixLocalSandboxClient())
                ),
            )
            observed[label] = "no error"
        except Exception as exc:
            observed[label] = f"{type(exc).__name__}: {str(exc)[:110]}"
    ok = (
        observed["default (Filesystem, Shell, Compaction)"].startswith("UserError: Hosted tools")
        and "context_management" in observed["Shell + Compaction"]
        and observed["Shell only"].startswith("APIConnectionError")
    )
    record(
        "P17",
        "Default sandbox capabilities need the Responses API; only Shell survives Chat Completions",
        "default -> apply_patch rejected; Compaction -> context_management kwarg rejected; Shell -> reaches network",
        observed,
        ok,
    )


PROBES = [
    probe_handoff_is_a_tool_and_shares_history,
    probe_nested_handoff_history,
    probe_agent_as_tool_isolation,
    probe_tool_error_redacted,
    probe_tool_not_found,
    probe_max_turns,
    probe_hitl_resume,
    probe_session_history_and_limit,
    probe_tool_output_trimmer,
    probe_stop_on_first_tool,
    probe_input_guardrails,
    probe_reset_tool_choice_and_dynamic_instructions,
    probe_parallel_tools,
    probe_sandbox_agent,
    probe_compaction_process_context,
    probe_parallel_guardrail_side_effect_race,
    probe_sandbox_capabilities_on_chat_completions,
]


async def main() -> int:
    for probe in PROBES:
        try:
            await probe()
        except Exception as exc:  # A crashing probe is a finding, not a script failure.
            RESULTS.append(
                {
                    "id": probe.__name__,
                    "pass": False,
                    "error": "".join(traceback.format_exception_only(type(exc), exc)).strip(),
                }
            )
            print(f"[ERROR] {probe.__name__}: {exc!r}")
            traceback.print_exc()
    import agents

    summary = {
        "sdk_version": getattr(agents, "__version__", None),
        "python": sys.version.split()[0],
        "passed": sum(1 for r in RESULTS if r.get("pass")),
        "total": len(RESULTS),
        "results": RESULTS,
    }
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("probe_output.json")
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n")
    print(f"\n{summary['passed']}/{summary['total']} probes matched the source-based prediction")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
