# miniagents: a minimal reproduction of the openai-agents-python architecture

Study: [research/openai-agents-python.md](../../research/openai-agents-python.md) ·
Upstream: [openai/openai-agents-python @ 71c2da4](https://github.com/openai/openai-agents-python/tree/71c2da4de47159ccc37905b8fe781be805dbfa66)

`miniagents` is about 1,900 lines of stdlib-only Python (about 1,700 without the demo) (asyncio, json, sqlite3, subprocess,
urllib). It reproduces the **architecture**, not the product: no streaming, tracing, MCP,
realtime, voice, hosted tools or provider zoo.

## What it reproduces

| miniagents | upstream (path at the pinned commit) | idea |
|---|---|---|
| `run.py::Runner.run` | `src/agents/run.py:1026` (`while True`) | one loop over a four-state `NextStep` machine; a turn = one model call + its side effects |
| `run.py::run_single_turn` | `src/agents/run_internal/run_loop.py:2665` | instructions, tools and handoffs resolved fresh every turn; input = caller items + replayable generated items |
| `run.py::process_model_response` | `src/agents/run_internal/turn_resolution.py:2926` | output items classified; a handoff is recognised by tool name only; unknown tool raises unless `tool_not_found_behavior="return_error_to_model"` |
| `run.py::execute_tools_and_side_effects` | `turn_resolution.py:804` | approvals planned first, then concurrent execution with outputs in model order; then handoff, `tool_use_behavior`, final output or run again |
| `run.py::execute_handoff` | `turn_resolution.py:537` | first handoff wins; default = full raw transcript; `input_filter` or `nest_handoff_history` change only the model view |
| `run.py::RunState` | `src/agents/run_state.py:835` | JSON pause point; agents re-bound by name; resume finishes the paused turn without a new model call |
| `agent.py::Agent.as_tool` | `src/agents/agent.py:606` | a `FunctionTool` that runs a nested `Runner.run` on the generated `input` only |
| `tool.py::function_tool` | `src/agents/tool.py:2574`, `:1980` | strict JSON schema from the signature; sync functions in `asyncio.to_thread`; failures become a fixed observation |
| `session.py` | `src/agents/memory/session.py:53` | four-method session protocol; history prepended; `limit` keeps the newest items |
| `trimmer.py::ToolOutputTrimmer` | `src/agents/extensions/tool_output_trimmer.py:88` | opt-in `call_model_input_filter` that shrinks old tool outputs in the model view only |
| `capabilities.py` | `src/agents/sandbox/runtime_agent_preparation.py:86`, `capabilities/*.py` | public agent vs per-run execution clone; `Shell`, `Skills` (index in prompt, body on disk), `Memory` (summary in prompt), `Compaction` (`context_management` + cut at compaction item) |
| `model.py::ChatCompletionsModel` | `src/agents/models/chatcmpl_converter.py:534`, `:1040` | items in, items out; handoffs become ordinary function tools at the wire |

## Layout

```text
src/miniagents/
  items.py         Responses-style items, RunItem metadata, orphan pruning
  model.py         Model protocol, ScriptedModel, stdlib Chat Completions adapter
  tool.py          FunctionTool, @function_tool, default error function
  agent.py         Agent, Handoff, handoff(), as_tool(), guardrails
  run.py           Runner loop, NextStep machine, RunState, approvals, handoffs
  session.py       InMemorySession, SQLiteSession, SessionSettings
  capabilities.py  Workspace, Capability, Shell, Skills, Memory, Compaction, CapableAgent
  trimmer.py       ToolOutputTrimmer
  demo.py          narrated end-to-end demo (11 checks)
tests/             40 tests, one file per architectural concern
mutation_check.py  injects 11 architectural regressions; each must fail a test
real_model/        run_ark.py drives the REAL upstream SDK; run_mini_ark.py drives miniagents
upstream_probe/    probe_openai_agents.py: 17 runtime probes of the real SDK (no network)
```

## Run

```bash
./run.sh          # venv -> pip install -e .[test] -> build wheel -> pytest -> demo
./run.sh demo     # demo only, no install
python3 mutation_check.py   # needs pytest on the interpreter (e.g. .venv/bin/python)
```

Real-model runs need an OpenAI-compatible endpoint (the study used Volcano Engine Ark):

```bash
# reproduction against a real model
ARK_API_KEY=... ARK_MODEL=deepseek-v4-1-flash-260910 python3 real_model/run_mini_ark.py --out mini.json
# the real upstream SDK against a real model (run from an SDK checkout at the pinned commit)
ARK_API_KEY=... ARK_MODEL=deepseek-v4-1-flash-260910 uv run --frozen python real_model/run_ark.py --out r.json
# the real upstream SDK with a scripted model (no network)
uv run --frozen python upstream_probe/probe_openai_agents.py out.json
```

Keys are read from the environment only. Result files contain model outputs, never keys.

## Verified results (this session, Python 3.13.16)

| Check | Result |
|---|---|
| `./run.sh` | install, wheel `miniagents-0.1.0-py3-none-any.whl`, **40 passed**, demo **11/11** |
| `mutation_check.py` | **11/11** mutations killed (each failed 1–4 tests); 40 passed after restore |
| `real_model/run_mini_ark.py` | **6/6** runs (deepseek-v4.1-flash ×3, doubao-seed-2.1-pro ×3): handoff, `as_tool` brief, approval pause, JSON round-trip, exactly one model call after resume |
| `upstream_probe/probe_openai_agents.py` | **17/17** predictions matched on the real SDK (0.23.1 + 22 commits) |
| `real_model/run_ark.py` | 6 scenarios × 2 models × 3 runs; see `real_model/results_*.json` and the study's Verification section |

## Known limitations

- No streaming path. Upstream keeps a second loop (`start_streaming`, `run_loop.py:969`) that must stay behaviourally aligned with the non-streaming one; miniagents has only one.
- `Workspace` is a temp directory plus `subprocess`, with no isolation, like `UnixLocalSandboxClient` on Linux. It is not a sandbox.
- No tracing, hooks, tool guardrails, timeouts, structured tool outputs, MCP, hosted tools or server-managed conversations.
- `RunState` stores agent names, not upstream's richer identity (duplicate names, nested agent-tool state, schema versions).
- `Compaction` only demonstrates the shape (`context_management` param + cut at a `compaction` item); no server does the compaction here.
