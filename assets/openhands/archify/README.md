# Archify sources for the OpenHands study

These JSON files are the authored Archify candidates behind
[`diagrams/openhands/`](../../../diagrams/openhands/). Nine diagrams are pinned to
`OpenHands/software-agent-sdk@54daf056bd863bb46f922a2fe9324dd736b37ff6` (tag `v1.53.0`, the
agent-server version Agent Canvas pins); `canvas-stack.json` is pinned to
`OpenHands/OpenHands@7ea83bab4fe71149b88b5a8a6b9efe9042cb362d`. In the rendered HTML, each node's
`SRC n` badge links to the exact GitHub lines.

| Diagram | Archify type | Gates (validate / deliver / strict check / browser) | visual-check | Verified source refs | Pinned to | Artifact sha256 (prefix) |
|---|---|---|---|---|---|---|
| `architecture.json` | architecture | pass / pass / pass / pass | pass | 16 | `54daf05` | `4ec0004ef87716b3` |
| `execution-flow.json` | sequence | pass / pass / pass / pass | pass | 11 | `54daf05` | `da8466d0a0024563` |
| `core-abstractions.json` | architecture | pass / pass / pass / pass | pass | 19 | `54daf05` | `3e68fc65c480fdd1` |
| `context-flow.json` | dataflow | pass / pass / pass / pass | pass | 24 | `54daf05` | `6ddeaa1e320f7286` |
| `agent-loop.json` | workflow | pass / pass / pass / pass | pass | 22 | `54daf05` | `742845a0d41e1e92` |
| `conversation-lifecycle.json` | lifecycle | pass / pass / pass / pass | pass | 9 | `54daf05` | `7978a424f51ce523` |
| `memory-flow.json` | dataflow | pass / pass / pass / pass | pass | 9 | `54daf05` | `a4210e9f40df6aeb` |
| `tool-runtime.json` | sequence | pass / pass / pass / pass | pass | 13 | `54daf05` | `d8d44d3fdd7c644c` |
| `sub-agent-flow.json` | sequence | pass / pass / pass / pass | pass | 7 | `54daf05` | `7fdbbfa67d84754b` |
| `canvas-stack.json` | architecture | pass / pass / pass / pass | pass | 16 | `7ea83ba` | `289f3c10207b7b83` |

All ten were finalized with `--quality showcase --repo-root <clone at the pinned commit>` and then
checked with `visual-check` (light and dark, 1440×900 and 2048×1320); the 1440×900 light
screenshots of architecture, core-abstractions, context-flow, execution-flow, agent-loop and
canvas-stack were inspected by eye. Machine-readable receipts (gates, SHA-256 of spec and artifact,
reference counts) are in [`receipts.json`](receipts.json).

## Regenerate

```bash
npx skills add tt-a1i/archify -g
git clone https://github.com/OpenHands/software-agent-sdk /tmp/software-agent-sdk
git -C /tmp/software-agent-sdk checkout 54daf056bd863bb46f922a2fe9324dd736b37ff6
git clone https://github.com/OpenHands/OpenHands /tmp/OpenHands
git -C /tmp/OpenHands checkout 7ea83bab4fe71149b88b5a8a6b9efe9042cb362d
export ARCHIFY_CHROME=/path/to/chrome
node ~/.agents/skills/archify/bin/archify.mjs finalize architecture \
  assets/openhands/archify/architecture.json diagrams/openhands/architecture.html \
  --repo-root /tmp/software-agent-sdk --quality showcase --json
# canvas-stack.json uses --repo-root /tmp/OpenHands
```

Run from the repository root (`meta.output` is relative to it). `*.delivery.json` sidecars hold
absolute local paths and are git-ignored.
