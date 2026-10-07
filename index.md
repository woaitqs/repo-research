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

| Repository | Research | Architecture | Execution Flow | All diagrams | Experiment | Status |
|---|---|---|---|---|---|---|
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) + [software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) | [Article](research/openhands.html) | [architecture](diagrams/openhands/architecture.html) | [execution-flow](diagrams/openhands/execution-flow.html) | [10 diagrams](diagrams/openhands/) | [experiments/openhands](https://github.com/woaitqs/repo-research/tree/main/experiments/openhands) | Done · verified 2026-10-07 |

## Reading convention

Each project uses the same URL pattern:

- Research: `/research/<project-name>.html`
- Diagrams: `/diagrams/<project-name>/` (index) and `/diagrams/<project-name>/<diagram>.html`
- Experiment source: `/experiments/<project-name>/` (on GitHub; experiments are not part of the site build)

Repository: [woaitqs/repo-research](https://github.com/woaitqs/repo-research)
