**English** · [中文](README.zh.md)

# minideer — minimal reproduction of the DeerFlow harness

A stdlib-only Python reproduction of the core architectural idea of
[`bytedance/deer-flow`](https://github.com/bytedance/deer-flow) (studied at commit
`cf8c29c`, harness `2.3.0-dev`):

> DeerFlow does not own the model/tool cycle. It assembles a **lead agent** — an
> ordered middleware stack plus a static system prompt — and hands it to LangChain's
> `create_agent`. Around that graph it owns a **run record**, a **per-thread sandbox**,
> and a context policy that keeps the system prompt stable: memory is a frozen
> reminder, compaction **deletes** old messages, and the summary is re-injected
> per call without being written back.

It reproduces the architecture, not the product. There is no real LLM:
`ScriptedModel` plays back deterministic actions and records every request it receives.

Research write-up: [`research/deer-flow.md`](../../research/deer-flow.md)

## What maps to what

| minideer | upstream deer-flow (`cf8c29c`) | Idea reproduced |
|---|---|---|
| `loop.py::Agent` | `lead_agent/agent.py` `create_agent` + `runtime/runs/worker.py` `agent.astream` | model → tools → model; fresh request every step; first middleware is the outermost wrapper; `after_model` runs in reverse registration order; run status is separate from the transcript |
| `harness.py::assemble_lead` | `build_middlewares` + `_insert_hitl_approval_middleware` | approval inserted at the front; clarification at the tail; skill **metadata** only in the system prompt; GitHub channel omits `update_agent` |
| `context.py::DynamicContextMiddleware` | `dynamic_context_middleware.py` | date and memory are a persisted `<system-reminder>`, written once; the same day is a no-op; midnight adds a date line and does not refresh memory |
| `context.py::SummarizationMiddleware` | `DeerFlowSummarizationMiddleware._maybe_summarize` | compacted messages are **removed** from the thread; `summary_text` survives |
| `context.py::DurableContextMiddleware` | `durable_context_middleware.py` | summary, delegation ledger and skills are re-rendered into the request only |
| `context.py::ToolOutputBudgetMiddleware` | `tool_output_budget_middleware.py` | outputs at or above the threshold are written to the sandbox; the model sees a stub |
| `context.py::SkillActivationMiddleware` | `skill_activation_middleware.py` | `/skill-name` loads the full body for one model call and does not store it |
| `sandbox.py` | `SandboxMiddleware` | lazy acquire on the first tool call; the lead owns `skill_view`; a child must not rebuild it |
| `subagents.py` | `task_tool` + `SubagentExecutor` | child messages are `[task]`; shared sandbox id; final text only; no `task` tool; `checkpointer is False` |
| `hitl.py` | `HitlApprovalMiddleware` + `ClarificationMiddleware` | approval is a serializable pause; non-interactive runs deny; clarification ends the turn (`goto=END`) and is not a resume |
| `memory.py` | `MemoryMiddleware` | facts are queued in `after_agent`, so a paused run has not written them yet |

Deliberately **not** reproduced: LangGraph checkpointing, SSE streaming, the real
DeerMem extractor, IM channel adapters, MCP, provisioner/K8s sandboxes, extension
loading, and authorization providers.

## Run

```bash
./run.sh          # venv -> pip install -e .[test] -> build wheel -> pytest -> demo
./run.sh demo     # demo only, no install (python3 >= 3.10)
python3 mutation_check.py   # 8 architectural regressions; each must fail a test
```

The demo walks a scripted `/triage` turn: a frozen memory reminder, one-shot skill
body, an externalized log, an isolated sub-agent that writes through the shared
sandbox, and a request-only durable block. It asserts 14 invariants and exits
non-zero if any fails.

## Tests (20)

| File | Pins down |
|---|---|
| `tests/test_loop.py` | loop exit, tool re-entry, stable system prompt, limiter-before-approval |
| `tests/test_context.py` | frozen reminder, midnight date line, destructive summary, dangling tool calls, offload, lazy sandbox |
| `tests/test_subagents.py` | message isolation, final-text return, shared sandbox, skill view, concurrency cap, no child checkpointer |
| `tests/test_hitl_skills_runs.py` | approval pause/resume, non-interactive deny, clarification vs resume, slash skill once, GitHub tool withholding, tool-mode memory |

## Limitations

- Tool calls in one model turn run **sequentially**. Upstream fans them out inside the tools node.
- Token counting is character length. Upstream's offload threshold is 12,000 characters (`externalize_min_chars`); the demo lowers it so the script stays small.
- A "thread" is the state dict passed back into `run`. The lead's `checkpointer` is an empty dict standing in for the object the worker attaches; the child is explicitly `False`.
- `bash` is an in-memory stub, not a shell. The sandbox is a dict of files keyed by thread id.
- Dangling tool calls are settled by appending a synthetic result. Upstream inserts that result immediately after the `AIMessage` inside `wrap_model_call` (`dangling_tool_call_middleware.py:16-18`), because a `before_model` update would land at the end of the list.
