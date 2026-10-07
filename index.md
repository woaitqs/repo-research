---
layout: default
title: Repo Research
---

# Repo Research

Source-code-level studies of important AI infrastructure and agent repositories.

Each study includes:

- Architecture overview
- Main execution flow
- Core abstractions
- Context / memory / tools / agents analysis
- Interactive Archify diagrams
- Five or more implementation decisions worth learning from
- A minimal runnable reproduction
- Verification notes

## Research Library

| Repository | Research | Architecture | Execution Flow | Experiment | Status |
|---|---|---|---|---|---|
| [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) @ `16e84d9` | [Read the study](research/deepagents.html) | [architecture.html](diagrams/deepagents/architecture.html) | [execution-flow.html](diagrams/deepagents/execution-flow.html) | [experiments/deepagents](https://github.com/woaitqs/repo-research/tree/main/experiments/deepagents) | Complete: 21 tests + demo pass, upstream probe 7/7, real-model scenarios 4/4 × 3 runs |
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) @ `7ea83ba` + [software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) @ `v1.53.0` | [Read the study](research/openhands.html) · [中文](research/openhands.zh.html) | [architecture.html](diagrams/openhands/architecture.html) | [execution-flow.html](diagrams/openhands/execution-flow.html) | [experiments/openhands](https://github.com/woaitqs/repo-research/tree/main/experiments/openhands) | Complete: 19 tests + demo pass, 426 upstream SDK tests pass, real SDK probed offline and with a real model |

### deepagents: all diagrams

[Architecture](diagrams/deepagents/architecture.html) ·
[Execution flow](diagrams/deepagents/execution-flow.html) ·
[Core abstractions](diagrams/deepagents/core-abstractions.html) ·
[Context flow](diagrams/deepagents/context-flow.html) ·
[Memory flow](diagrams/deepagents/memory-flow.html) ·
[Tool runtime](diagrams/deepagents/tool-runtime.html) ·
[Agent loop](diagrams/deepagents/agent-loop.html) ·
[Sub-agent flow](diagrams/deepagents/sub-agent-flow.html)

> One-line takeaway: deepagents is an ordered stack of middleware on top of LangChain's `create_agent`.
> Its real product is context engineering: offloading large results to a virtual filesystem,
> non-destructive summarization, and context-isolated sub-agents.

### OpenHands: all diagrams

[Gallery](diagrams/openhands/) ·
[Architecture](diagrams/openhands/architecture.html) ·
[Execution flow](diagrams/openhands/execution-flow.html) ·
[Core abstractions](diagrams/openhands/core-abstractions.html) ·
[Context flow](diagrams/openhands/context-flow.html) ·
[Agent loop](diagrams/openhands/agent-loop.html) ·
[Conversation lifecycle](diagrams/openhands/conversation-lifecycle.html) ·
[Memory flow](diagrams/openhands/memory-flow.html) ·
[Tool runtime](diagrams/openhands/tool-runtime.html) ·
[Sub-agent flow](diagrams/openhands/sub-agent-flow.html) ·
[Canvas local stack](diagrams/openhands/canvas-stack.html)

> One-line takeaway: `OpenHands/OpenHands` is now the Agent Canvas UI. The agent itself lives in
> `software-agent-sdk`: an event-sourced conversation, a stateless agent `step()`, an LLM context that is
> a projection of the event log, and compaction done by appending a `Condensation` event.
>
> 一句话结论（[中文版研究](research/openhands.zh.html)）：`OpenHands/OpenHands` 现在是 Agent Canvas 界面；agent 本体在
> `software-agent-sdk` 中——事件溯源的会话、无状态的 `step()`、作为事件日志投影的 LLM 上下文，以及通过追加 `Condensation` 事件完成的压缩。

## Reading convention

Each project uses the same URL pattern:

- Research: `/research/<project-name>.html`
- Diagrams: `/diagrams/<project-name>/<diagram>.html` (plus a gallery at `/diagrams/<project-name>/` where provided)
- Experiment source: [`/experiments/<project-name>/`](https://github.com/woaitqs/repo-research/tree/main/experiments) (on GitHub; experiments are not published to Pages)

Repository: [woaitqs/repo-research](https://github.com/woaitqs/repo-research)
