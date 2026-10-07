---
layout: research
title: "OpenHands — interactive diagrams"
permalink: /diagrams/openhands/
---

# OpenHands — interactive diagrams / 交互式图表

Ten [Archify](https://github.com/tt-a1i/archify) diagrams. Each node carries `SRC` badges
that link to the exact lines they were drawn from. Nine are pinned to
[`OpenHands/software-agent-sdk@54daf05`](https://github.com/OpenHands/software-agent-sdk/tree/54daf056bd863bb46f922a2fe9324dd736b37ff6)
(tag `v1.53.0`, the agent-server version Agent Canvas pins) and one to
[`OpenHands/OpenHands@7ea83ba`](https://github.com/OpenHands/OpenHands/tree/7ea83bab4fe71149b88b5a8a6b9efe9042cb362d).

10 张 [Archify](https://github.com/tt-a1i/archify) 图。每个节点都带有 `SRC` 标记，链接到绘制依据的具体源码行。
其中 9 张固定在 `software-agent-sdk@54daf05`（标签 `v1.53.0`，即 Agent Canvas 固定的 agent-server 版本），
1 张固定在 `OpenHands/OpenHands@7ea83ba`。图中文字为英文，中英文文章共用同一套图。

| Diagram | Type | What it answers | 回答的问题 | Pinned to |
|---|---|---|---|---|
| [architecture.html](architecture.html) | architecture | How Canvas, Agent Server, SDK and runtime fit together | Canvas、Agent Server、SDK 和运行时如何组合 | software-agent-sdk |
| [execution-flow.html](execution-flow.html) | sequence | What happens from `POST /api/conversations` to an `ObservationEvent` on the WebSocket | 从 `POST /api/conversations` 到 WebSocket 上出现 `ObservationEvent` 之间发生了什么 | software-agent-sdk |
| [core-abstractions.html](core-abstractions.html) | architecture | Which object owns what (Conversation, State, EventLog, View, Agent, LLM, Tool, Workspace, Condenser) | 每个对象分别拥有什么（Conversation、State、EventLog、View、Agent、LLM、Tool、Workspace、Condenser） | software-agent-sdk |
| [context-flow.html](context-flow.html) | dataflow | What enters the model context, what is excluded, how it is bounded | 什么进入模型上下文、什么被排除、如何限制大小 | software-agent-sdk |
| [agent-loop.html](agent-loop.html) | workflow | `LocalConversation.run()` + `Agent.step()`, early returns and recovery | `LocalConversation.run()` + `Agent.step()`，提前返回与恢复 | software-agent-sdk |
| [conversation-lifecycle.html](conversation-lifecycle.html) | lifecycle | `ConversationExecutionStatus` transitions | `ConversationExecutionStatus` 的状态转换 | software-agent-sdk |
| [memory-flow.html](memory-flow.html) | dataflow | Two-tier `MEMORY.md`: writer, loader, injection, staleness | 两层 `MEMORY.md`：谁写、谁加载、如何注入、如何处理过时 | software-agent-sdk |
| [tool-runtime.html](tool-runtime.html) | sequence | Tool call → validation → confirmation → executor → workspace → observation | 工具调用 → 校验 → 确认 → 执行器 → 工作区 → 观察结果 | software-agent-sdk |
| [sub-agent-flow.html](sub-agent-flow.html) | sequence | `TaskTool` delegation and context isolation | `TaskTool` 委托与上下文隔离 | software-agent-sdk |
| [canvas-stack.html](canvas-stack.html) | architecture | What `agent-canvas` launches and how the ingress routes | `agent-canvas` 启动了什么、入口代理如何路由 | OpenHands |

Each diagram passed `archify finalize --quality showcase --repo-root <pinned checkout>`
(schema validation, verified delivery, strict provenance check, real-browser check)
and `archify visual-check` (light/dark, 1440×900 and 2048×1320). Receipts with
specification/artifact SHA-256 and the number of verified source references are in
[`assets/openhands/archify/receipts.json`](https://github.com/woaitqs/repo-research/blob/main/assets/openhands/archify/receipts.json).
The JSON specifications (Archify candidates) and a regeneration recipe are in [`assets/openhands/archify/`](https://github.com/woaitqs/repo-research/tree/main/assets/openhands/archify).

每张图都通过了 `archify finalize --quality showcase --repo-root <固定版本的检出目录>`（schema 校验、可验证交付、严格的来源检查、
真实浏览器检查）和 `archify visual-check`（亮色/暗色，1440×900 与 2048×1320）。包含规格/产物 SHA-256 和已验证源码引用数的回执见
`receipts.json`；JSON 规格与重新生成步骤见 `assets/openhands/archify/`（链接同上）。

← Back to the research article / 返回研究文章：[English](../../research/openhands.html) · [中文](../../research/openhands.zh.html)
