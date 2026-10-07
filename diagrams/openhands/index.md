---
layout: research
title: "OpenHands — interactive diagrams"
permalink: /diagrams/openhands/
---

# OpenHands — interactive diagrams

Ten [Archify](https://github.com/tt-a1i/archify) diagrams. Each node carries `SRC` badges
that link to the exact lines they were drawn from. Nine are pinned to
[`OpenHands/software-agent-sdk@54daf05`](https://github.com/OpenHands/software-agent-sdk/tree/54daf056bd863bb46f922a2fe9324dd736b37ff6)
(tag `v1.53.0`, the agent-server version Agent Canvas pins) and one to
[`OpenHands/OpenHands@7ea83ba`](https://github.com/OpenHands/OpenHands/tree/7ea83bab4fe71149b88b5a8a6b9efe9042cb362d).

| Diagram | Type | What it answers | Pinned to |
|---|---|---|---|
| [architecture.html](architecture.html) | architecture | How Canvas, Agent Server, SDK and runtime fit together | software-agent-sdk |
| [execution-flow.html](execution-flow.html) | sequence | What happens from `POST /api/conversations` to an `ObservationEvent` on the WebSocket | software-agent-sdk |
| [core-abstractions.html](core-abstractions.html) | architecture | Which object owns what (Conversation, State, EventLog, View, Agent, LLM, Tool, Workspace, Condenser) | software-agent-sdk |
| [context-flow.html](context-flow.html) | dataflow | What enters the model context, what is excluded, how it is bounded | software-agent-sdk |
| [agent-loop.html](agent-loop.html) | workflow | `LocalConversation.run()` + `Agent.step()`, early returns and recovery | software-agent-sdk |
| [conversation-lifecycle.html](conversation-lifecycle.html) | lifecycle | `ConversationExecutionStatus` transitions | software-agent-sdk |
| [memory-flow.html](memory-flow.html) | dataflow | Two-tier `MEMORY.md`: writer, loader, injection, staleness | software-agent-sdk |
| [tool-runtime.html](tool-runtime.html) | sequence | Tool call → validation → confirmation → executor → workspace → observation | software-agent-sdk |
| [sub-agent-flow.html](sub-agent-flow.html) | sequence | `TaskTool` delegation and context isolation | software-agent-sdk |
| [canvas-stack.html](canvas-stack.html) | architecture | What `agent-canvas` launches and how the ingress routes | OpenHands |

Each diagram passed `archify finalize --quality showcase --repo-root <pinned checkout>`
(schema validation, verified delivery, strict provenance check, real-browser check)
and `archify visual-check` (light/dark, 1440×900 and 2048×1320). Receipts with
specification/artifact SHA-256 and the number of verified source references are in
[`assets/openhands/archify/receipts.json`](https://github.com/woaitqs/repo-research/blob/main/assets/openhands/archify/receipts.json).
The JSON specifications (Archify candidates) and a regeneration recipe are in [`assets/openhands/archify/`](https://github.com/woaitqs/repo-research/tree/main/assets/openhands/archify).

← Back to the [research article](../../research/openhands.html)
