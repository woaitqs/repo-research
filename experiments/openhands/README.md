# mini_openhands — minimal reproduction of the OpenHands V1 agent core

This is a small (~1.3k non-blank lines including docstrings and the demo), dependency-free Python package that rebuilds the **architectural
core** of OpenHands (the `openhands-sdk` package in
[`OpenHands/software-agent-sdk`](https://github.com/OpenHands/software-agent-sdk),
which is what `OpenHands/OpenHands` / Agent Canvas runs underneath). It reproduces
the architecture, not the product: no UI, no Docker, no LiteLLM.

What it reproduces, and where each idea comes from:

| Idea | mini_openhands | Upstream (software-agent-sdk @ v1.53.0) |
|---|---|---|
| Event-sourced conversation, one JSON file per event | `event_log.py`, `state.py` | `conversation/event_store.py`, `conversation/state.py`, `persistence_const.py` |
| Small mutable snapshot (`base_state.json`) autosaved on field change | `state.py` (`__setattr__`) | `ConversationState.__setattr__` / `_save_base_state` |
| Stateless agent; one `step()` per loop iteration; effects only via `on_event` | `agent.py` | `agent/agent.py` (`Agent.step`, `_step`) |
| Conversation owns loop, lock, status machine, max iterations | `conversation.py` | `conversation/impl/local_conversation.py` (`_run`) |
| LLM context = projection (`View`) of the log, not the log itself | `view.py` | `context/view/view.py`, `ConversationState.view` |
| Condensation is an appended event (forgotten ids + summary + offset) | `condenser.py`, `events.Condensation` | `context/condenser/llm_summarizing_condenser.py`, `event/condenser.py` |
| Cut points that never split a tool call from its result | `View.manipulation_indices` | `context/view/properties/*` |
| Context overflow -> `CondensationRequest` -> hard condensation | `agent.py` | `Agent._step` `except LLMContextWindowExceedError` |
| Bad tool calls become model-visible `AgentErrorEvent`s (loop continues) | `agent._to_action_events` | `Agent._get_action_event`, `_emit_tool_error` |
| Confirmation mode via unmatched actions; second `run()` = approval | `state.get_unmatched_actions`, `agent.step` | `ConversationState.get_unmatched_actions`, `Agent._requires_user_confirmation` |
| Tool spec (data) -> registry factory -> executable definition | `tools.py` | `tool/spec.py`, `tool/registry.py`, `tool/tool.py` |
| Large outputs: head + tail kept, middle cut, full text offloaded to `observations/` | `tools.bound_output` | `utils/truncate.py` `maybe_truncate(save_dir=...)`, TerminalObservation.to_llm_content |
| Workspace boundary (local now, remote/docker later) | `workspace.py` | `workspace/base.py`, `workspace/remote/base.py` |
| Resume from disk; tools may be added, never removed | `Conversation.__init__`, `Agent.verify` | `ConversationState.create`, `AgentBase.verify` |
| Stuck detection on repeated action/observation | `stuck.py` | `conversation/stuck_detector.py` |
| One model boundary, retries, normalized overflow error | `llm.py` | `llm/llm.py`, `llm/utils/retry_mixin.py` |

## Layout

```text
experiments/openhands/
├── README.md
├── pyproject.toml
├── run.sh                      # install + build + tests + offline demo (+ --live)
├── src/mini_openhands/
│   ├── events.py               # typed immutable events + events_to_messages
│   ├── event_log.py            # file-backed append-only log
│   ├── state.py                # ConversationState: base_state.json + log + view
│   ├── view.py                 # projection + manipulation indices
│   ├── condenser.py            # rolling summarizing condenser
│   ├── tools.py                # ToolSpec / registry / ToolDefinition / built-ins
│   ├── workspace.py            # execution boundary
│   ├── llm.py                  # LLM protocol, ScriptedLLM, OpenAI-compatible client
│   ├── agent.py                # stateless Agent.step()
│   ├── conversation.py         # loop, lock, statuses, persistence, resume
│   ├── stuck.py                # stuck detector
│   └── demo.py                 # end-to-end demo (scripted or --live)
├── tests/                      # 19 behaviour tests (pytest)
└── probe/
    ├── probe_real_sdk.py       # runtime probe of the *real* SDK, scripted TestLLM
    └── probe_real_sdk_live.py  # same, with a real OpenAI-compatible model
```

## Run

```bash
./run.sh            # creates .venv, pip install -e .[test], compileall, pytest, scripted demo
./run.sh --live     # additionally runs the demo against a real OpenAI-compatible model
```

`--live` reads `ARK_API_KEY` (required), `ARK_BASE_URL` (default
`https://ark.cn-beijing.volces.com/api/coding/v3`, Volcano Engine Ark Coding Plan, OpenAI-compatible)
and `ARK_MODEL` (default `doubao-seed-2-1-pro-260915`) from the environment. Any OpenAI-compatible
endpoint works. Never commit keys; `.env` is git-ignored.

Note: Ark's `ark-code-latest` router intermittently answered an identical request with
`400 InvalidParameter: messages.tool_calls.type` (1 of 4 replays on 2026-10-07), so the default
is pinned to a concrete model.

## What the scripted demo shows

1. `file_editor create` → `terminal python3 fib.py` → a malformed JSON tool call
   (becomes `AgentErrorEvent`, loop continues) → `seq 1 50000` (observation is
   bounded to ~2 KB, full output written to `observations/`) → condensation fires
   (`forgot=8 offset=2`) → `finish`.
2. A **new** `Conversation` object with the same id resumes from disk, sees 15
   events, does not re-emit the system prompt, and continues the conversation.

## Probe of the real SDK

`probe/probe_real_sdk.py` drives the real `openhands-sdk` v1.53.0 (`LocalConversation`
+ `TerminalTool` + `FileEditorTool` + `LLMSummarizingCondenser`) with the SDK's own
scripted `TestLLM`, and prints what was persisted. Run it from a
`software-agent-sdk` checkout at `v1.53.0`:

```bash
uv sync --frozen
OPENHANDS_SUPPRESS_BANNER=1 uv run --frozen python /path/to/probe_real_sdk.py /tmp/probe-out
```

The sanitized output we recorded is in
[`assets/openhands/real-sdk-probe-report.json`](../../assets/openhands/real-sdk-probe-report.json).

`probe/probe_real_sdk_live.py` does the same against a real OpenAI-compatible model through
LiteLLM (`openai/<model>` with `base_url`), reading `ARK_API_KEY` / `ARK_BASE_URL` / `ARK_MODEL`
and `PROBE_MAX_SIZE` (condenser `max_size`) from the environment.

## Verification (2026-10-07)

| Check | Command | Result |
|---|---|---|
| install + build | `./run.sh` (venv, `pip install -e ".[test]"`, `compileall`) | ok |
| tests | `python -m pytest -q` | **19 passed** |
| offline demo | `python -m mini_openhands.demo` | finished; `Condensation forgot=8 offset=2`; resumed from disk with 15 events, 19 after the second turn |
| live demo | `ARK_API_KEY=... python -m mini_openhands.demo --live` | `doubao-seed-2-1-pro-260915`: finished, 42 events, 4 real condensations, recovered from its own invalid `file_editor` calls (`AgentErrorEvent`s; at least 2 in the captured tail), `fib.py` + `test_fib.py` created ([log](../../assets/openhands/mini-openhands-live-run.txt)) |
| real SDK, offline | `probe/probe_real_sdk.py` | see [report](../../assets/openhands/real-sdk-probe-report.json) |
| real SDK, live | `probe/probe_real_sdk_live.py` (`PROBE_MAX_SIZE=12` and `8`) | see [report](../../assets/openhands/real-sdk-live-ark-report.json) |

## Known limitations (by design)

* Synchronous only; no `arun()`, streaming, interrupt, or parallel tool execution.
* No conversation tree / fork (`parent_id`, `leaf_event_id`) — the log is linear.
* Validation is a tiny JSON-schema subset, not Pydantic Action models.
* Token-based condensation is replaced by an event-count threshold.
* No security analyzer, hooks, skills, MCP, sub-agents or agent-server; those are
  documented in the research article instead.
