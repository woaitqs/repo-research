# minilc — minimal reproduction of the letta-code architecture

A ~1,100-line (docstrings included, demo excluded), **stdlib-only** Python reproduction of the
core architectural idea of [`letta-ai/letta-code`](https://github.com/letta-ai/letta-code)
(studied at commit `4b028fa`, v0.34.4):

> The agent loop is **split across a contract**. A stateful *backend* runs exactly **one model
> step per run** and stops with `requires_approval` whenever the model wants a tool. A *client
> harness* classifies permissions, executes the tools locally, and starts the next run with an
> `approval` message. Memory is a **git repository compiled into the system prompt from HEAD**,
> kept byte-stable for prompt caching, with a one-shot delta when a new commit lands.

It reproduces the architecture, not the product. `ScriptedModel` plays the LLM and records
every request, so each claim can be checked exactly. `OpenAICompatModel` talks to any
OpenAI-compatible endpoint.

Research write-up: [`research/letta-code.md`](../../research/letta-code.md)

## What maps to what

| minilc | upstream letta-code (`4b028fa`) | Idea reproduced |
|---|---|---|
| `backend.py::Backend`, `LocalBackend` | `src/backend/backend.ts` (`Backend`), `local/local-backend.ts`, `dev/fake-headless-backend.ts` | one contract, swappable backend; one active run per conversation; one model step per run; tool calls → `approval_request_message` + `requires_approval`; dangling tool calls settled with "Turn did not complete"; bounded overflow-compaction (≤3) and transient retries (≤3) |
| `transcript.py` | `local/local-store.ts`, `local/local-transcript.ts` | append-only JSONL; compaction appends a row and replaces only `in_context_ids` |
| `compaction.py` | `dev/provider-turn-executor.ts:241-272`, `local/compaction.ts` | threshold `window − min(16384, 20%)`; sliding window (30%, +10% steps) cutting only at assistant messages; summary re-enters as a user-role `system_alert` |
| `memfs.py` | `local/system-prompt-compilation.ts` | v2 layout: root `*.md` = core memory, `MEMORY.md` index, child dirs deferred; **read from `git show HEAD:`** only; `<memory_update>` delta |
| `harness.py::Harness` | `src/headless.ts:2081-2667`, `agent/message.ts`, `cli/helpers/stream.ts` | client turn loop; `client_tools`/`client_skills` on every request; `end_turn` with approvals coerced to `requires_approval`; max-turns ignores approval continuations |
| `permissions.py` | `permissions/checker.ts`, `mode.ts`, `cli/helpers/approval-classification.ts` | allow/deny/ask before execution; deny rules and the cross-agent guard beat `unrestricted` (the upstream default); one-shot headless denies `ask` |
| `tools.py` | `tools/manager.ts`, `agent/approval-execution.ts`, `tools/impl/truncation.ts` | errors as observations; PreToolUse hooks block/rewrite; secret scrubbing; 32k clamp with overflow file; read-only tools in parallel, `Edit`/`Write` per-file lock, shell under a global lock |
| `reminders.py` | `reminders/engine.ts` | volatile context as `<system-reminder>` parts on the user message, never in the system prompt; none for sub-agents |
| `subagents.py` | `agent/subagents/manager.ts`, `tools/impl/task.ts`, `subagent-depth.ts` | `Agent` tool; fresh child = new memory-less agent seeing only its brief; `fork` = copy of the parent conversation + "NOT the primary agent"; report-only return; depth limit 2 |

Deliberately **not** reproduced: streaming token deltas, run resume/replay, Letta Cloud,
pi-ai provider adapters, hooks other than PreToolUse, OS sandboxing, reflection/dreaming,
memory workers and worktrees, skills discovery, MCP, mods, channels, and the TUI.
`minilc` runs children in-process; upstream spawns a separate `letta` process.

## Run

```bash
./run.sh          # venv -> pip install -e .[test] -> build wheel -> pytest -> demo
./run.sh demo     # demo only, no install (python3 >= 3.10, git on PATH)
python3 mutation_check.py   # inject 9 architectural regressions; each must fail a test
```

## Upstream checks (need a letta-code clone at `4b028fa` with `bun install`)

```bash
./run.sh probe /path/to/letta-code        # drives the real LocalBackend (no network)
```

`upstream_probe/probe_letta_code.ts` drives the real `LocalBackend` with a capturing executor.
It checks the claims below, and its output is in `upstream_probe/probe_output.json`.

| Probe | Claim | Result |
|---|---|---|
| P1 | tool call ends the run with `requires_approval`; approval becomes a `toolResult` | held |
| P2 | an uncommitted memory edit does not reach the model | held |
| P3a | first call after a memory commit carries `<memory_update>`; system prompt bytes unchanged | held |
| P3b | later calls in the same conversation still see the committed memory | **did not hold** |
| P4a | compaction appends a row; earlier transcript rows untouched; view shrinks | held |
| P4b | after compaction: summary as user-role `system_alert`, prompt re-rendered with new memory | held |
| P5 | dangling tool call settled with a synthetic error result | held |
| P6 | second turn during an active run is rejected | held |
| P7 | threshold = window − min(16384, 20%) | held |

## Real model (`real_model/`)

`run_letta_real.py` drives the **published bundle** (`letta.js` built from `4b028fa`) in
headless mode with `--backend local` against a real model. It uses Volcano Engine Ark's
`deepseek-v4-1-flash` through `ark_shim.py`, a logging OpenAI-compatible shim. The shim
exists because Ark's `/models` list is empty, so letta-code's provider discovery cannot find the
model. The shim also records every request body, which shows what actually entered the model
context on each call. `ARK_API_KEY` is read from the environment and never logged.

```bash
bun install && bun run build                  # in the letta-code clone -> letta.js
ARK_API_KEY=... python3 real_model/run_letta_real.py --letta-js /path/to/letta.js --out results.json
```

Three full runs are saved in `results_run{1,2,3}.json`. `sample_request_S1.json` contains a
complete first request: system prompt, `<system-reminder>` parts and tool-schema sizes.
`provider_usage.json` lists every provider call of the three runs, in order: request shape,
system-prompt hash, and the provider-reported prompt, cached and max-completion tokens. It was
extracted from the shim logs with `extract_usage.py` and copies no message text.

Two observations come from `provider_usage.json`:

- **Cache hits.** At least 98% of each S1 call's prompt tokens were cached.
- **Prefix break.** The one S2 call that carried `<memory_update>` had only about 22% cached. pi-ai had folded the update into the system message for this model, which changed the prefix.

| Scenario | Checks (3 runs) |
|---|---|
| S1 tool loop (Read → Write) | 5/5 checks pass in 3/3 runs: one run per model step, prompt byte-identical, schemas on every call, reminders on the user message |
| S2 memory (save → recall same conversation → recall new conversation) | 5/5 in 3/3: the commit reaches the next call once; later calls in the same conversation do **not** carry it; a new conversation's prompt does |
| S3a sub-agent, one-shot `-p` | 6/6 in 3/3: child is a separate memory-less agent seeing only its brief; the parent ends its turn before the report arrives |
| S3b sub-agent, bidirectional stream-json | 4/4 in 3/3: the report comes back as a `<task-notification>` turn; child context never enters the parent |
| S4a compaction across turns (45k window) | compaction + append-only transcript + summary as `system_alert`: 3/3; recall of both facts in turn C: 2/3 |
| S4b compaction stress (both reads in one step, 40k window) | compaction fires 3/3; the answer is lost 3/3 (`max_tokens_exceeded`) |

## Known limitations

- One model (`deepseek-v4-1-flash`), small tasks, 3 runs each. This is evidence, not a benchmark.
- S4 lowers the conversation's window to 40–45k tokens by editing the stored conversation record between CLI runs (`letta model set --model-settings` updates only `model_settings`, and the top-level `context_window_limit` takes precedence). This deliberately stresses the design; 128k+ windows rarely hit this path.
- `minilc` runs tool batches with threads and has no streaming, resume, or OS sandbox.
