# Archify sources for the letta-code study

These JSON files are the authored Archify candidates behind
[`diagrams/letta-code/`](../../../diagrams/letta-code/). Every node carries `sources`
pinned to `letta-ai/letta-code@4b028fab07c69edaac2ddb4f7b9a43573ff20d81`. In the rendered
HTML, each node's `SRC n` badge links to the exact GitHub lines.

| Diagram | Archify type | Gates (validate / deliver / strict check / browser) | Artifact sha256 (prefix) |
|---|---|---|---|
| `architecture.json` | architecture | pass / pass / pass / pass | `5b2c3e885ce089d7` |
| `execution-flow.json` | sequence | pass / pass / pass / pass | `1c488611fabb3e92` |
| `core-abstractions.json` | architecture | pass / pass / pass / pass | `ad07a9ea4b3af47c` |
| `context-flow.json` | dataflow | pass / pass / pass / pass | `b05880bf8b7e49b7` |
| `memory-flow.json` | dataflow | pass / pass / pass / pass | `1765f0cbfc59c8d7` |
| `tool-runtime.json` | workflow (v2) | pass / pass / pass / pass | `e0ad84117438b3d8` |
| `agent-loop.json` | lifecycle (v2) | pass / pass / pass / pass | `bc636a325da955e2` |
| `sub-agent-flow.json` | sequence | pass / pass / pass / pass | `d865eb68f8171671` |

All eight were finalized with Archify 3.0.1, `--quality showcase --repo-root <clone at the pinned commit>`.
The browser gate used headless Chromium. Screenshots of every diagram (light theme,
1440×900) were captured with `visual-check` and inspected by eye.

Repair notes, recorded so later edits don't reintroduce the same failures:

- **Sequence:** messages that share horizontal space need ≥ 28px of `y` spacing. At width 1080, the canvas must stay ≤ 696px tall, so the content was compacted to fit.
- **Dataflow:** two flows entering the same side of a node fan their ports out by ±7px, which the showcase gate rejects as a micro-segment. Give each extra input its own side with an explicit `via` corner.
- **Dataflow legend:** the auto legend calls dashed flows "async batch". Both dataflows override that label, because here dashed means a side write or a conditional path.

## Regenerate

```bash
npx skills add tt-a1i/archify -g          # installs the skill (bin/archify.mjs)
git clone https://github.com/letta-ai/letta-code /tmp/letta-code
git -C /tmp/letta-code checkout 4b028fab07c69edaac2ddb4f7b9a43573ff20d81
export ARCHIFY_CHROME=/path/to/chrome      # needed for the browser gate
node ~/.agents/skills/archify/bin/archify.mjs finalize architecture \
  assets/letta-code/archify/architecture.json diagrams/letta-code/architecture.html \
  --repo-root /tmp/letta-code --quality showcase --json
```

Run the command from the repository root, because `meta.output` paths are relative to it.
Delivery writes `*.delivery.json`, `*.finalize*.json` and `*.browser-check.json` receipts
next to each HTML file. They hold absolute local paths, so they are git-ignored. Delete a
diagram's old `*.browser-check.json` before re-finalizing it; the browser gate refuses to
overwrite evidence it does not own.
