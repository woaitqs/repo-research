# minideep — minimal reproduction of the deepagents harness

A ~1,200-line (docstrings included, demo excluded), **stdlib-only** Python reproduction of the core architectural idea of
[`langchain-ai/deepagents`](https://github.com/langchain-ai/deepagents) (studied at commit
`16e84d9`, SDK `0.7.22`):

> A deep agent is not a new runtime. It is a **plain tool-calling loop** plus an
> **ordered stack of middleware** that rewrites each model request, wraps each tool call,
> and keeps context bounded by moving bulk content into a **pluggable virtual filesystem**.

It reproduces the architecture, not the product. There is no real LLM:
`ScriptedModel` plays back deterministic actions and records every request it receives,
so each architectural claim can be checked exactly.

Research write-up: [`research/deepagents.md`](../../research/deepagents.md)

## What maps to what

| minideep | upstream deepagents (commit `16e84d9`) | Idea reproduced |
|---|---|---|
| `loop.py::Agent` | `langchain/agents/factory.py::create_agent` (LangChain, which deepagents calls) | model → tools → model loop; fresh `ModelRequest` every step; onion-composed `wrap_model_call`/`wrap_tool_call`; exits when no tool calls |
| `middleware.py` | `langchain.agents.middleware.types.AgentMiddleware` | `before_agent`, `wrap_model_call`, `wrap_tool_call`, contributed `tools`; per-call edits vs. explicit `state_update` |
| `graph.py::create_deep_agent` | `libs/deepagents/deepagents/graph.py::create_deep_agent` | stack order; auto `general-purpose` sub-agent; sub-agents get no `task` tool |
| `backends.py` | `deepagents/backends/{protocol,state,filesystem,local_shell,composite}.py` | `StateBackend` writes into graph state through the runtime; `CompositeBackend` longest-prefix routing; `execute` capability probe |
| `filesystem.py` | `deepagents/middleware/filesystem.py::FilesystemMiddleware` | hide `execute` without a shell; offload results larger than 20k tokens to `/large_tool_results/<id>` with a head/tail preview; same-path mutation guard |
| `summarization.py` | `deepagents/middleware/summarization.py` | **non-destructive** compaction via `_summarization_event`; history offloaded to `/conversation_history/<session>.md`; safe cutoff; `ContextOverflowError` fallback |
| `subagents.py` | `deepagents/middleware/subagents.py::_build_task_tool` | `task` tool; isolated child context; only final text returns; shared `files` merge back |
| `memory.py` | `deepagents/middleware/{memory,skills}.py` | AGENTS.md loaded once per thread into the system prompt; skills show metadata only (progressive disclosure) |
| `graph.py::PatchToolCallsMiddleware` | `deepagents/middleware/patch_tool_calls.py` | repairs tool calls left without a result by an interrupted run |

Deliberately **not** reproduced: LangGraph checkpointing, streaming and interrupts (HITL),
async/remote sub-agents, forked sub-agents, harness/provider profiles, prompt caching,
filesystem permissions, multimodal handling, and capture-at-source `execute` offload.

## Run

```bash
./run.sh          # venv -> pip install -e .[test] -> build wheel -> pytest -> demo
./run.sh demo     # demo only, no install (python3 >= 3.10)
```

The demo walks a scripted triage of a failing test suite. Along the way it
offloads a 170 KB `execute` output, greps the offloaded file, reads a skill on demand,
delegates to an isolated sub-agent, pages a log until summarization fires twice, and
persists a preference to memory routed into state. It finishes by asserting 10 invariants
and exits non-zero if any of them fails.

## Tests (21)

| File | Pins down |
|---|---|
| `tests/test_loop.py` | loop exit condition, parallel tool calls, errors as observations, onion order, per-call prompt edits never stored |
| `tests/test_filesystem.py` | `execute` gating, offload and paging back, `read_file` never offloaded, parallel-mutation guard, composite routing and artifacts root |
| `tests/test_summarization.py` | full history kept in state, bounded view, chained offload file, safe cutoff, reactive overflow path |
| `tests/test_subagents.py` | isolation, only final text returns, `files` flow back, no recursion, private state not shared |
| `tests/test_memory_and_skills.py` | memory injection, **memory staleness within a thread**, skill metadata only, dangling tool-call patch |

## Upstream probe (verifies claims against the *real* SDK)

`upstream_probe/probe_deepagents.py` drives the real `deepagents.create_deep_agent` with a
scripted fake `BaseChatModel`. It needs no API key or network, and runs 7 checks.
`upstream_probe/probe_output.json` is the recorded output.

```bash
python3 -m venv /tmp/da && /tmp/da/bin/pip install "deepagents==0.7.22"   # or -e <clone>/libs/deepagents
ANTHROPIC_API_KEY=dummy /tmp/da/bin/python upstream_probe/probe_deepagents.py
```

| Check | Observed (deepagents 0.7.22) |
|---|---|
| P1 default main stack | `Filesystem, SubAgent, Summarization, PatchToolCalls, AnthropicPromptCaching, UnsupportedContent` |
| P2 `execute` with `StateBackend` | hidden from the model |
| P3 170k-char tool result | written to `/large_tool_results/call_big_1`; model sees a 1,812-char stub |
| P4 sub-agent input / output | `[SystemMessage, HumanMessage(description)]` in; only `"Report written to /report.md"` out; child's `/report.md` present in parent `files`; child has no `task` tool |
| P5 summarization | state keeps 44 messages; `_summarization_event.cutoff_index = 39`; last model call sees 6 messages |
| P6 memory | after `edit_file` changes AGENTS.md to "likes rust", turn 2's system prompt still says "likes python" |
| P7 delete inside isolated sub-agent | child reports `Deleted /old.md`, yet `/old.md` is still in the parent's `files` |

## Limitations

- Tool calls in one model turn run **sequentially**. Upstream fans them out in parallel,
  one LangGraph `Send` per call.
- Token counting is `chars / 4`. Upstream uses `count_tokens_approximately` and counts tool schemas too.
- A "thread" is just passing the returned state back into `invoke`. There is no checkpointer.
- `ShellBackend` runs commands on the host, like upstream `LocalShellBackend`. Its path
  checks are not a sandbox.
- Summarization thresholds are absolute tokens or message counts. Upstream derives fractions
  (trigger 85% / keep 10%) from the model's context window. The demo also shows what goes
  wrong when the keep window is too close to the trigger: compaction runs on every call.
