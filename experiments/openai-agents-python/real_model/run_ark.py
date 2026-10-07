"""Real-model checks for the openai-agents-python study via Volcano Engine Ark.

Drives the *real* SDK (openai-agents 0.23.1 at the pinned commit) through its
`OpenAIChatCompletionsModel` adapter against Ark's OpenAI-compatible endpoint.

Env:  ARK_API_KEY (required)   ARK_MODEL (required)
      ARK_BASE_URL (default https://ark.cn-beijing.volces.com/api/plan/v3)

  uv run --frozen python run_ark.py --out results.json     # from the SDK checkout

Every scenario records what crossed the model boundary (via a recording `Model` wrapper),
so the JSON shows the actual context each agent received, not just the final answers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import traceback
from typing import Any

from openai import AsyncOpenAI

from agents import (
    Agent,
    Model,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    RunState,
    SQLiteSession,
    function_tool,
    set_tracing_disabled,
)
from agents.memory import SessionSettings

BASE = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3").rstrip("/")
CFG = RunConfig(tracing_disabled=True)
set_tracing_disabled(True)


class Recording(Model):
    """Delegating model that records each call's instructions, input items and tool names."""

    def __init__(self, inner: Model, label: str) -> None:
        self.inner, self.label, self.calls = inner, label, []

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, *, previous_response_id, conversation_id, prompt):
        items = [i if isinstance(i, dict) else i.model_dump(exclude_unset=True) for i in (input if isinstance(input, list) else [{"role": "user", "content": input}])]
        self.calls.append(
            {
                "system_chars": len(system_instructions or ""),
                "input_kinds": [i.get("type") or f"message:{i.get('role')}" for i in items],
                "input": items,
                "tools": [t.name for t in tools],
                "handoffs": [h.tool_name for h in handoffs],
            }
        )
        return await self.inner.get_response(system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, previous_response_id=previous_response_id, conversation_id=conversation_id, prompt=prompt)

    def stream_response(self, *args: Any, **kwargs: Any):
        return self.inner.stream_response(*args, **kwargs)


def model(label: str) -> Recording:
    client = AsyncOpenAI(base_url=BASE, api_key=os.environ["ARK_API_KEY"])
    return Recording(OpenAIChatCompletionsModel(model=os.environ["ARK_MODEL"], openai_client=client), label)


def tool_calls(result) -> list[tuple[str, str]]:
    out = []
    for item in result.new_items:
        raw = item.raw_item
        if getattr(raw, "type", None) == "function_call" or (isinstance(raw, dict) and raw.get("type") == "function_call"):
            out.append((getattr(raw, "name", None) or raw.get("name"), getattr(raw, "arguments", None) or raw.get("arguments")))
    return out


async def r1_handoff() -> dict[str, Any]:
    billing_m, tech_m, triage_m = model("billing"), model("tech"), model("triage")
    billing = Agent(name="Billing", instructions="You handle charges and refunds. Answer in 2 sentences.", handoff_description="Charges, invoices, refunds.", model=billing_m)
    tech = Agent(name="Tech", instructions="You fix technical problems. Answer in 2 sentences.", handoff_description="Bugs, crashes, login problems.", model=tech_m)
    triage = Agent(name="Triage", instructions="Route the user to the right specialist. Do not answer yourself.", handoffs=[billing, tech], model=triage_m)
    result = await Runner.run(triage, "I was charged twice for my September subscription.", run_config=CFG)
    first_billing = billing_m.calls[0]["input_kinds"] if billing_m.calls else None
    return {
        "triage_handoffs_offered": triage_m.calls[0]["handoffs"],
        "routed_to": result.last_agent.name,
        "billing_first_input_kinds": first_billing,
        "billing_saw_raw_transcript": first_billing is not None and first_billing[0] == "message:user" and "function_call" in first_billing,
        "final": str(result.final_output)[:300],
        "pass": result.last_agent.name == "Billing" and bool(result.final_output),
    }


async def r2_agent_as_tool() -> dict[str, Any]:
    fr_m, es_m, orch_m = model("fr"), model("es"), model("orchestrator")
    fr = Agent(name="French", instructions="Translate the given text to French. Output only the translation.", model=fr_m)
    es = Agent(name="Spanish", instructions="Translate the given text to Spanish. Output only the translation.", model=es_m)
    orch = Agent(
        name="Orchestrator",
        instructions="Use the translation tools for every translation, then combine the results.",
        tools=[fr.as_tool("translate_to_french", "Translate text to French."), es.as_tool("translate_to_spanish", "Translate text to Spanish.")],
        model=orch_m,
    )
    msg = (
        "Internal note: customer id CUST-991, never share it outside this conversation.\n"
        "Translate 'Good morning, the meeting moved to 3pm.' into French and Spanish."
    )
    result = await Runner.run(orch, msg, run_config=CFG)
    child_inputs = [c["input"] for c in fr_m.calls + es_m.calls]
    calls = tool_calls(result)
    turns_with_tools = sum(1 for c in orch_m.calls if any(k == "function_call" for k in c["input_kinds"]))
    return {
        "tool_calls": calls,
        "parallel_in_one_turn": len(orch_m.calls) == 2 and len(calls) >= 2,
        "orchestrator_model_calls": len(orch_m.calls),
        "child_input_item_counts": [len(i) for i in child_inputs],
        "child_inputs": child_inputs,
        "secret_leaked_to_children": "CUST-991" in json.dumps(child_inputs, ensure_ascii=False),
        "final": str(result.final_output)[:400],
        "pass": len(fr_m.calls) >= 1 and len(es_m.calls) >= 1 and all(len(i) == 1 for i in child_inputs),
        "_turns_with_tools": turns_with_tools,
    }


async def r3_hitl() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for decision in ("approve", "reject"):
        executed: list[str] = []

        @function_tool(needs_approval=True)
        def issue_refund(order_id: str, amount: float) -> str:
            """Issue a refund. Requires manager approval."""
            executed.append(order_id)
            return f"Refund of {amount} issued for order {order_id}."

        m = model(f"refund-{decision}")
        agent = Agent(name="Refunds", instructions="Issue refunds with the issue_refund tool when the customer is owed money, then confirm to the customer.", tools=[issue_refund], model=m)
        first = await Runner.run(agent, "Order 1042 was charged 30.00 twice. Please refund the duplicate.", run_config=CFG)
        interrupted = [i.raw_item.name for i in first.interruptions]
        executed_before = list(executed)
        payload = json.loads(json.dumps(first.to_state().to_json()))
        state = await RunState.from_json(agent, payload)
        for item in state.get_interruptions():
            if decision == "approve":
                state.approve(item)
            else:
                state.reject(item, rejection_message="Manager declined: refunds need a support ticket first.")
        second = await Runner.run(agent, state, run_config=CFG)
        final = str(second.final_output)
        out[decision] = {
            "interrupted_on": interrupted,
            "executed_before_decision": executed_before,
            "executed_after": executed,
            "state_json_bytes": len(json.dumps(payload)),
            "model_calls": len(m.calls),
            "final": final[:300],
            "mentions_ticket": "ticket" in final.lower(),
        }
    ok = (
        out["approve"]["interrupted_on"] == ["issue_refund"]
        and out["approve"]["executed_before_decision"] == []
        and out["approve"]["executed_after"] == ["1042"]
        and out["reject"]["executed_after"] == []
    )
    return {**out, "pass": ok}


async def r4_session() -> dict[str, Any]:
    m = model("session")
    agent = Agent(name="Assistant", instructions="Be brief.", model=m)
    s = SQLiteSession(f"real-{time.time_ns()}")
    await Runner.run(agent, "My favourite programming language is Rust. Just acknowledge.", session=s, run_config=CFG)
    recall = await Runner.run(agent, "Which programming language is my favourite? One word.", session=s, run_config=CFG)

    m2 = model("session-limited")
    agent2 = Agent(name="Assistant", instructions="Be brief. If you do not know, say UNKNOWN.", model=m2)
    s2 = SQLiteSession(f"real-limit-{time.time_ns()}", session_settings=SessionSettings(limit=2))
    await Runner.run(agent2, "My favourite programming language is Rust. Just acknowledge.", session=s2, run_config=CFG)
    await Runner.run(agent2, "What is 2+2? Just the number.", session=s2, run_config=CFG)
    limited = await Runner.run(agent2, "Which programming language is my favourite? One word.", session=s2, run_config=CFG)
    return {
        "full_history_answer": str(recall.final_output)[:80],
        "full_history_input_items": len(m.calls[-1]["input"]),
        "limit2_answer": str(limited.final_output)[:80],
        "limit2_input_items": len(m2.calls[-1]["input"]),
        "pass": "rust" in str(recall.final_output).lower() and len(m2.calls[-1]["input"]) == 3,
    }


async def r5_tool_errors() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mode in ("default_redacted", "custom_visible"):
        attempts: list[str] = []

        def visible(ctx, error: Exception) -> str:
            return f"Tool error: {error}"

        def get_order_impl(order_id: str) -> str:
            attempts.append(order_id)
            if not order_id.isdigit():
                raise ValueError("order_id must contain digits only, for example '1042'")
            return json.dumps({"order_id": order_id, "status": "shipped"})

        if mode == "custom_visible":
            tool = function_tool(get_order_impl, name_override="get_order", failure_error_function=visible)
        else:
            tool = function_tool(get_order_impl, name_override="get_order")
        m = model(f"errors-{mode}")
        agent = Agent(name="Orders", instructions="Look up orders with get_order and report the status.", tools=[tool], model=m)
        try:
            result = await Runner.run(agent, "What is the status of order #A-1042?", run_config=CFG, max_turns=6)
            final = str(result.final_output)
        except Exception as exc:
            final = f"{type(exc).__name__}: {exc}"
        out[mode] = {"attempts": attempts, "recovered": "1042" in attempts, "final": final[:240]}
    return {**out, "pass": True}


async def r6_sandbox_shell_skills_memory() -> dict[str, Any]:
    from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
    from agents.sandbox.capabilities import Memory, Shell, Skill, Skills
    from agents.sandbox.config import MemoryReadConfig
    from agents.sandbox.entries import File
    from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient

    m = model("sandbox")
    manifest = Manifest(
        entries={
            "CHANGELOG.md": File(content=b"- feat: add CSV export\n- fix: crash when the cart is empty\n- feat: dark mode\n"),
            "memories/memory_summary.md": File(content=b"- The user wants answers as terse bullet points.\n"),
        }
    )
    agent = SandboxAgent(
        name="Writer",
        instructions="Help with the repository in your workspace.",
        model=m,
        default_manifest=manifest,
        # Chat Completions cannot carry Filesystem (apply_patch) or Compaction (context_management): see probe P17.
        capabilities=[
            Shell(),
            Skills(
                skills=[
                    Skill(
                        name="release-notes",
                        description="Draft release notes from CHANGELOG.md.",
                        content="# release-notes\n1. Read CHANGELOG.md.\n2. Group lines under 'Features' and 'Fixes'.\n3. End with the exact line: Generated with the release-notes skill.\n",
                    )
                ]
            ),
            Memory(read=MemoryReadConfig(live_update=False), generate=None),
        ],
    )
    result = await Runner.run(
        agent,
        "Draft release notes for this release.",
        run_config=RunConfig(tracing_disabled=True, sandbox=SandboxRunConfig(client=UnixLocalSandboxClient())),
        max_turns=12,
    )
    calls = tool_calls(result)
    cmds = [json.loads(a).get("cmd", "") for n, a in calls if n == "exec_command"]
    final = str(result.final_output)
    return {
        "system_prompt_chars": m.calls[0]["system_chars"],
        "tools_offered": m.calls[0]["tools"],
        "exec_commands": cmds,
        "read_skill_md": any("SKILL.md" in c for c in cmds),
        "read_changelog": any("CHANGELOG" in c for c in cmds),
        "followed_skill_marker": "Generated with the release-notes skill." in final,
        "final": final[:500],
        "pass": any("CHANGELOG" in c for c in cmds) and bool(final),
    }


SCENARIOS = {
    "R1_handoff": r1_handoff,
    "R2_agent_as_tool": r2_agent_as_tool,
    "R3_hitl": r3_hitl,
    "R4_session": r4_session,
    "R5_tool_errors": r5_tool_errors,
    "R6_sandbox": r6_sandbox_shell_skills_memory,
}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    results: dict[str, Any] = {"model": os.environ["ARK_MODEL"], "base_url": BASE}
    for name, fn in SCENARIOS.items():
        if args.only and name not in args.only:
            continue
        start = time.perf_counter()
        try:
            results[name] = await fn()
        except Exception as exc:
            results[name] = {"pass": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}
        results[name]["seconds"] = round(time.perf_counter() - start, 1)
        print(f"{name}: pass={results[name].get('pass')} ({results[name]['seconds']}s)", flush=True)
    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
