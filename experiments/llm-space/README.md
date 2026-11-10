**English** · [中文](README.zh.md)

# minispace — minimal reproduction of the LLM Space workbench

A stdlib-only Python reproduction of the core architectural idea of
[`deer-flow/llm-space`](https://github.com/deer-flow/llm-space) (studied at commit
`342c9f5`, desktop `4.20.0`):

> LLM Space is a desktop workbench, not an agent that runs tools inside the model
> loop. It asks Pi Agent Core for **one model step**. Every tool executor returns
> immediately with terminate set, so Pi stops. The workbench then decides: leave
> the call for a human, run the executable tools once, or keep alternating up to
> a cap. Prompt templates stay in the thread file. A snapshot freezes what an
> earlier run already rendered. Compaction writes a **new** message list. Run
> bodies live beside the workspace, and an evaluation is a human score.

It reproduces the architecture, not the product. There is no real LLM:
`ScriptedModel` plays back deterministic actions and records every request.

Research write-up: [`research/llm-space.md`](../../research/llm-space.md)

## What maps to what

| minispace | upstream llm-space (`342c9f5`) | Idea reproduced |
|---|---|---|
| `loop.py::PiStep` | `packages/core/src/server/agent/stream.ts` `_convertToAgentTools` | a tool call ends the borrowed loop; the step does not run the tool |
| `workbench.py::Workbench.run` | `packages/ui/.../stores/thread-store.ts` `run` | `step` stops; `auto` executes once; `react` continues, capped at 50 model turns |
| `tools.py` | `isExecutableTool`, `isDangerousBashCommand` | function tools, `ask_user_question`, `spawn_agent`, and a risky `bash` stay human |
| `render.py` | `renderThreadPromptVariables` | the stored template is unchanged; an existing snapshot place wins |
| `compaction.py` | `planCompaction` / `applyCompactionPreview` / `nextCompactedThreadPath` | keep recent user turns; a checkpoint is a new user message; the source list is not edited; names stay in one sequence |
| `storage.py::Home` | `LocalFileSystem` + `RunHistoryStore` | run bodies leave the workspace file; delete keeps them; orphans expire |
| `subagent.py` | `buildSubagentThread` | a sibling thread, system and meta kept, parent transcript dropped, child not run |
| `evaluation.py` | `saveEvaluation` | scores are 1–5, the rubric is copied, and no model is called |
| remote plugin path | `apps/desktop/src/client/tool-execution.ts` | a plugin tool on a non-local runtime becomes an error result and the plugin body is not called |

Deliberately **not** reproduced: Electrobun, the real Pi stream, MCP transports, the plugin subprocess, Langfuse import, the LangGraph project generator, and provider-hosted tools.

## Run

```bash
./run.sh          # venv -> pip install -e .[test] -> build wheel -> pytest -> demo
./run.sh demo     # demo only, no install (python3 >= 3.10)
python3 mutation_check.py   # 9 architectural regressions; each must fail a test
```

The demo walks a scripted triage turn: a frozen date placeholder, a ReAct pair of `skill` then `bash`, a run archived outside the workspace, a compaction sibling, and a child thread that is not executed. It asserts 21 invariants and exits non-zero if any fails.

## Tests (25)

| File | Pins down |
|---|---|
| `tests/test_loop.py` | step vs auto vs react, human gates, dangerous bash, remote plugin, the 50-turn cap, which conversations can start |
| `tests/test_context.py` | stored templates, snapshot reuse, a new place rendered with the new value |
| `tests/test_compaction_storage.py` | verbatim tail, progressive checkpoint, sibling names, truncated tool text, history that outlives delete |
| `tests/test_subagent_eval.py` | child isolation, role tool filters, rubric copy, scores outside 1–5 |

## Limitations

- One scripted model stands in for both Pi and the provider. Upstream's model call is `models.streamSimple` inside `agentLoopContinue`.
- Placeholder syntax in this reproduction is the doubled-brace form used by the app. The Nunjucks includes and conditionals are not reproduced.
- A "thread" is a dict. Upstream persists JSON under `~/.llm-space/workspace` and hashes run files under `history/`.
- Tool executors are plain functions. Upstream fans a batch with `Promise.all` and routes builtin, MCP, and plugin calls through different clients.
- The cap matches the playground: 50 model turns, and the 50th turn's tools still run.
