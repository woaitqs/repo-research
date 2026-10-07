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
| [letta-ai/letta-code](https://github.com/letta-ai/letta-code) @ `4b028fa` | [Read the study](research/letta-code.html) | [architecture.html](diagrams/letta-code/architecture.html) | [execution-flow.html](diagrams/letta-code/execution-flow.html) | [experiments/letta-code](https://github.com/woaitqs/repo-research/tree/main/experiments/letta-code) | Complete: 24 tests + demo 11/11, mutation 9/9, upstream unit tests 8,778 pass (7 failures traced to the container), probe 9 claims (1 disproved), real-model 6 scenarios × 3 runs |

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

### letta-code: all diagrams

[Architecture](diagrams/letta-code/architecture.html) ·
[Execution flow](diagrams/letta-code/execution-flow.html) ·
[Core abstractions](diagrams/letta-code/core-abstractions.html) ·
[Context flow](diagrams/letta-code/context-flow.html) ·
[Memory flow](diagrams/letta-code/memory-flow.html) ·
[Tool runtime](diagrams/letta-code/tool-runtime.html) ·
[Agent loop](diagrams/letta-code/agent-loop.html) ·
[Sub-agent flow](diagrams/letta-code/sub-agent-flow.html)

> One-line takeaway: letta-code splits the agent loop across a protocol. A stateful backend
> (Letta Cloud or an in-process local backend) runs one model step per run and stops at every
> tool call; the client harness executes tools on the user's machine and resumes. Memory is a
> per-agent git repository compiled into a cache-stable system prompt.

## Reading convention

Each project uses the same URL pattern:

- Research: `/research/<project-name>.html`
- Diagrams: `/diagrams/<project-name>/<diagram>.html`
- Experiment source: [`/experiments/<project-name>/`](https://github.com/woaitqs/repo-research/tree/main/experiments) (on GitHub; experiments are not published to Pages)

Repository: [woaitqs/repo-research](https://github.com/woaitqs/repo-research)
