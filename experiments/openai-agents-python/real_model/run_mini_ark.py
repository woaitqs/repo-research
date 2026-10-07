"""Drive the *reproduction* (miniagents) with a real model through its stdlib Chat Completions adapter.

Env: ARK_API_KEY, ARK_MODEL, ARK_BASE_URL (default https://ark.cn-beijing.volces.com/api/plan/v3)

    PYTHONPATH=../src python3 run_mini_ark.py --out mini_results.json

Scenario (one chain): Triage hands off to Refunds; Refunds consults a Policy sub-agent through
Agent.as_tool and asks to issue a refund; the refund tool needs approval, so the run pauses;
the RunState goes through JSON, is approved, and resumes to a final answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from miniagents import Agent, ChatCompletionsModel, Runner, RunState, function_tool  # noqa: E402

BASE = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3")


def model() -> ChatCompletionsModel:
    return ChatCompletionsModel(os.environ["ARK_MODEL"], base_url=BASE, api_key=os.environ["ARK_API_KEY"])


async def scenario() -> dict:
    refunds_issued: list[str] = []

    @function_tool
    def lookup_order(order_id: str) -> str:
        """Look up an order by its numeric id."""
        return json.dumps({"order_id": order_id, "charges": [30.0, 30.0], "status": "paid"})

    @function_tool(needs_approval=True)
    def issue_refund(order_id: str, amount: float) -> str:
        """Refund money to the customer. Requires approval."""
        refunds_issued.append(order_id)
        return f"refunded {amount} on order {order_id}"

    policy_m, refunds_m, triage_m = model(), model(), model()
    policy = Agent(name="Policy", instructions="Answer refund-policy questions in one sentence. Duplicate charges are always refundable.", model=policy_m)
    refunds = Agent(
        name="Refunds",
        instructions=(
            "You resolve refunds. First look up the order and ask the policy tool whether the "
            "situation is refundable. If it is, call issue_refund for the duplicate amount, then "
            "confirm to the customer in one sentence."
        ),
        handoff_description="Handles refunds and double charges.",
        model=refunds_m,
        tools=[lookup_order, policy.as_tool("check_policy", "Ask the refund-policy expert a question."), issue_refund],
    )
    triage = Agent(name="Triage", instructions="Route the customer to the right specialist. Do not answer yourself.", model=triage_m, handoffs=[refunds])

    first = await Runner.run(triage, "I was charged twice (30.00 each) on order 1042. Please fix it.", max_turns=12)
    interrupted = [i.item["name"] for i in first.interruptions]
    before = list(refunds_issued)
    final = first.final_output
    resumed_calls = None
    if first.interruptions:
        state = RunState.from_json(triage, json.loads(json.dumps(first.to_state().to_json())))
        for item in state.get_interruptions():
            state.approve(item)
        calls_before_resume = len(refunds_m.calls)
        second = await Runner.run(triage, state, max_turns=12)
        final = second.final_output
        resumed_calls = len(refunds_m.calls) - calls_before_resume
    return {
        "triage_handoffs_offered": triage_m.calls[0].handoffs,
        "routed_to_refunds": bool(refunds_m.calls),
        "refunds_first_input_kinds": [i.get("type") or i.get("role") for i in refunds_m.calls[0].input] if refunds_m.calls else None,
        "policy_inputs": [c.input for c in policy_m.calls],
        "refunds_tool_calls": [i["name"] for c in refunds_m.calls for i in c.input if i.get("type") == "function_call"][-6:],
        "interrupted_on": interrupted,
        "executed_before_approval": before,
        "executed_after": refunds_issued,
        "model_calls_after_resume": resumed_calls,
        "final": str(final)[:300],
        "pass": interrupted == ["issue_refund"] and before == [] and refunds_issued == ["1042"] and bool(final),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    results = {"model": os.environ["ARK_MODEL"], "runs": []}
    for n in range(args.runs):
        start = time.perf_counter()
        try:
            run = await scenario()
        except Exception as exc:
            run = {"pass": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
        run["seconds"] = round(time.perf_counter() - start, 1)
        results["runs"].append(run)
        print(f"run {n + 1}: pass={run['pass']} ({run['seconds']}s) {run.get('error', '')}", flush=True)
    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
