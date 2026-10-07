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
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) @ `71c2da4` | [Read the study](research/openai-agents-python.html) | [architecture.html](diagrams/openai-agents-python/architecture.html) | [execution-flow.html](diagrams/openai-agents-python/execution-flow.html) | [experiments/openai-agents-python](https://github.com/woaitqs/repo-research/tree/main/experiments/openai-agents-python) | Complete: upstream 12,174 tests pass, probe 17/17, 40 tests + demo 11/11, mutations 11/11, real-model 6 scenarios × 2 models × 3 runs |

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

### openai-agents-python: all diagrams

[Architecture](diagrams/openai-agents-python/architecture.html) ·
[Execution flow](diagrams/openai-agents-python/execution-flow.html) ·
[Core abstractions](diagrams/openai-agents-python/core-abstractions.html) ·
[Context flow](diagrams/openai-agents-python/context-flow.html) ·
[Memory flow](diagrams/openai-agents-python/memory-flow.html) ·
[Tool runtime](diagrams/openai-agents-python/tool-runtime.html) ·
[Agent loop](diagrams/openai-agents-python/agent-loop.html) ·
[Sub-agent flow](diagrams/openai-agents-python/sub-agent-flow.html)

> One-line takeaway: the OpenAI Agents SDK owns its loop: a `while True` over four `NextStep` outcomes,
> where handoffs and sub-agents are just tool calls and a human approval is a serializable pause.
> Context policy is opt-in hooks plus server-side compaction; `SandboxAgent` adds per-run capabilities
> and agent-written file memory.

## Reading convention

Each project uses the same URL pattern:

- Research: `/research/<project-name>.html`
- Diagrams: `/diagrams/<project-name>/<diagram>.html`
- Experiment source: [`/experiments/<project-name>/`](https://github.com/woaitqs/repo-research/tree/main/experiments) (on GitHub; experiments are not published to Pages)

Repository: [woaitqs/repo-research](https://github.com/woaitqs/repo-research)
