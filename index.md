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

## Reading convention

Each project uses the same URL pattern:

- Research: `/research/<project-name>.html`
- Diagrams: `/diagrams/<project-name>/<diagram>.html`
- Experiment source: [`/experiments/<project-name>/`](https://github.com/woaitqs/repo-research/tree/main/experiments) (on GitHub; experiments are not published to Pages)

Repository: [woaitqs/repo-research](https://github.com/woaitqs/repo-research)
