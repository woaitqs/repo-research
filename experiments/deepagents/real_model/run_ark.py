"""Real-model checks for the deepagents study via Volcano Engine Ark (OpenAI-compatible).

Env:  ARK_API_KEY (required)  ARK_BASE_URL (default https://ark.cn-beijing.volces.com/api/plan/v3)
      ARK_MODEL   (required for --scenarios; --check lists models if the endpoint supports it)

  python run_ark.py --check        # verify the key: list models (if supported) + one tiny completion
  python run_ark.py --scenarios    # drive the real deepagents SDK with the real model
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

BASE = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3").rstrip("/")


def _http(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['ARK_API_KEY']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https endpoint
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check() -> int:
    status, text = _http("GET", "/models")
    print(f"GET /models -> {status}: {text[:800]}")
    model = os.environ.get("ARK_MODEL")
    if not model:
        print("Set ARK_MODEL to run a test completion.")
        return 0 if status == 200 else 1
    status, text = _http("POST", "/chat/completions", {"model": model, "messages": [{"role": "user", "content": "Reply with the word OK."}], "max_tokens": 8})
    print(f"POST /chat/completions ({model}) -> {status}: {text[:800]}")
    return 0 if status == 200 else 1


def scenarios() -> int:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import InMemorySaver

    from deepagents import create_deep_agent
    from deepagents.backends.utils import create_file_data

    def model(**profile: int) -> ChatOpenAI:
        m = ChatOpenAI(model=os.environ["ARK_MODEL"], base_url=BASE, api_key=os.environ["ARK_API_KEY"], use_responses_api=False, temperature=0)
        if profile:
            m.profile = {**(m.profile or {}), **profile}
        return m

    def tool_calls(state: dict) -> list[tuple[str, dict]]:
        return [(c["name"], c["args"]) for m in state["messages"] if isinstance(m, AIMessage) for c in m.tool_calls]

    results: dict[str, object] = {}

    # S1: large result offloaded -> does the real model follow the pointer?
    @tool
    def run_tests() -> str:
        """Run the project's test suite and return the full log."""
        return "\n".join(f"tests/test_{i}.py::test_case {'FAILED AssertionError: ids off by one' if i == 1734 else 'PASSED'}" for i in range(3000))

    s1 = create_deep_agent(model=model(), tools=[run_tests]).invoke({"messages": [HumanMessage("Run the tests and tell me the exact name of the failing test.")]})
    calls = tool_calls(s1)
    results["S1_tool_calls"] = calls
    results["S1_followed_pointer"] = any(n in ("read_file", "grep") and "large_tool_results" in json.dumps(a) for n, a in calls)
    results["S1_answer"] = s1["messages"][-1].content[:300]
    results["S1_correct"] = "test_1734" in s1["messages"][-1].content

    # S2: delegation -> how does the real model brief a sub-agent?
    s2 = create_deep_agent(model=model()).invoke(
        {
            "messages": [HumanMessage("Use the general-purpose subagent to write a 3-bullet summary of /notes.md into /summary.md, then tell me it is done.")],
            "files": {"/notes.md": create_file_data("Deep agents offload big tool results.\nSummaries keep a pointer to full history.\nSubagents isolate context.")},
        }
    )
    briefs = [a.get("description", "") for n, a in tool_calls(s2) if n == "task"]
    results["S2_task_briefs"] = briefs
    results["S2_summary_written"] = "/summary.md" in s2.get("files", {})

    # S3: memory -> does the real model persist a preference with edit_file?
    s3 = create_deep_agent(model=model(), memory=["/AGENTS.md"]).invoke(
        {"messages": [HumanMessage("From now on, always answer in Rust when I ask for code examples. Please remember this.")], "files": {"/AGENTS.md": create_file_data("# Memory\n")}}
    )
    results["S3_memory_tool_calls"] = [(n, a.get("file_path")) for n, a in tool_calls(s3) if n in ("edit_file", "write_file")]
    agents_md = s3["files"]["/AGENTS.md"]["content"]
    results["S3_agents_md_after"] = agents_md if isinstance(agents_md, str) else "\n".join(agents_md)

    # S4: summarization with a tiny window -> is a fact from the summarized span still usable?
    @tool
    def read_chunk(i: int) -> str:
        """Read chunk i of a long report."""
        fact = " The deployment codename is BLUE-HERON." if i == 0 else ""
        return f"chunk {i}:{fact} " + "filler text " * 400

    s4_agent = create_deep_agent(model=model(max_input_tokens=12_000), tools=[read_chunk], checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "s4"}, "recursion_limit": 200}
    s4 = s4_agent.invoke({"messages": [HumanMessage("Read chunks 0 through 7 one at a time with read_chunk, then tell me the deployment codename.")]}, cfg)
    state = s4_agent.get_state(cfg).values
    event = state.get("_summarization_event")
    results["S4_summarized"] = event is not None
    chunk0 = next((i for i, m in enumerate(state["messages"]) if isinstance(m, ToolMessage) and "BLUE-HERON" in str(m.content)), None)
    results["S4_chunk0_index"] = chunk0
    results["S4_cutoff_index"] = event["cutoff_index"] if event else None
    results["S4_chunk0_was_summarized_away"] = bool(event) and chunk0 is not None and chunk0 < event["cutoff_index"]
    results["S4_fact_in_summary"] = bool(event) and "BLUE-HERON" in str(event["summary_message"].content)
    results["S4_state_messages"] = len(state["messages"])
    results["S4_answer"] = s4["messages"][-1].content[:300]
    results["S4_correct"] = "BLUE-HERON" in s4["messages"][-1].content
    results["S4_read_history_file"] = any(n == "read_file" and "conversation_history" in json.dumps(a) for n, a in tool_calls(s4))

    json.dump(results, sys.stdout, indent=2, ensure_ascii=False, default=str)
    print()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--scenarios", action="store_true")
    args = parser.parse_args()
    sys.exit(check() if args.check or not args.scenarios else scenarios())
