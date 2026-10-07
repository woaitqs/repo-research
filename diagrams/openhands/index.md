---
layout: gallery
title: "OpenHands — interactive diagrams"
study: openhands
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

<!-- diagram-cards -->

The diagram list (titles, types, questions, pins) lives in
[`_data/studies.yml`](https://github.com/woaitqs/repo-research/blob/main/_data/studies.yml).

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
