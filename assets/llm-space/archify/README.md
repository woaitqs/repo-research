# Archify sources for the llm-space study

These JSON files are the authored Archify candidates behind
[`diagrams/llm-space/`](../../../diagrams/llm-space/). Every node carries `sources`
pinned to `deer-flow/llm-space@342c9f5761f969247e971eb36317aadf0bcc9daf`. In the
rendered HTML, each node's `SRC n` badge links to the exact GitHub lines.

| Diagram | Archify type | Gates (validate / deliver / strict check / browser) | Spec sha256 (prefix) |
|---|---|---|---|
| `architecture.json` | architecture | pass / pass / pass / pass | `82bc2801f62168e8` |
| `execution-flow.json` | sequence | pass / pass / pass / pass | `564d76bdcdabd1ec` |
| `core-abstractions.json` | architecture | pass / pass / pass / pass | `6fa8754b25bd0d6e` |
| `context-flow.json` | dataflow | pass / pass / pass / pass | `14b78a254a3dbb15` |
| `memory-flow.json` | dataflow | pass / pass / pass / pass | `3552650475ec67b4` |
| `tool-runtime.json` | workflow (v2) | pass / pass / pass / pass | `b1cafc5ae237710d` |
| `agent-loop.json` | lifecycle (v3) | pass / pass / pass / pass | `28a3f7ac5f5980fa` |
| `sub-agent-flow.json` | sequence | pass / pass / pass / pass | `07a44d175dd0afc3` |

All eight were finalized with `--quality showcase --repo-root <clone at the pinned commit>`.
The browser gate used headless Chrome. `agent-loop` keeps two return edges (ReAct back to
the model step, and a later run after a human gate). Those edges cross; the gates still pass.

## Regenerate

```bash
npx skills add tt-a1i/archify -g
git clone https://github.com/deer-flow/llm-space /tmp/llm-space
git -C /tmp/llm-space checkout 342c9f5761f969247e971eb36317aadf0bcc9daf
export ARCHIFY_CHROME=/usr/bin/google-chrome
node ~/.agents/skills/archify/bin/archify.mjs finalize architecture \
  assets/llm-space/archify/architecture.json diagrams/llm-space/architecture.html \
  --repo-root /tmp/llm-space --quality showcase --json
```

Run the command from the repository root, because `meta.output` paths are relative to it.
The first argument is the Archify type (`architecture`, `sequence`, `dataflow`, `workflow`, `lifecycle`),
not the file name. Delivery writes `*.delivery.json` provenance sidecars next to each HTML file.
They hold absolute local paths, so they are git-ignored.
