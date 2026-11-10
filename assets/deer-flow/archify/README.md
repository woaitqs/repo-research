# Archify sources for the deer-flow study

These JSON files are the authored Archify candidates behind
[`diagrams/deer-flow/`](../../../diagrams/deer-flow/). Every node carries `sources`
pinned to `bytedance/deer-flow@cf8c29c6b6920671075bd9010b94d89df2a273a8`. In the
rendered HTML, each node's `SRC n` badge links to the exact GitHub lines.

| Diagram | Archify type | Gates (validate / deliver / strict check / browser) | Spec sha256 (prefix) |
|---|---|---|---|
| `architecture.json` | architecture | pass / pass / pass / pass | `d86647d99563b172` |
| `execution-flow.json` | sequence | pass / pass / pass / pass | `434f15086b01c07b` |
| `core-abstractions.json` | architecture | pass / pass / pass / pass | `8073b4ee3a55b48e` |
| `context-flow.json` | dataflow | pass / pass / pass / pass | `fea6a4cdf5641964` |
| `memory-flow.json` | dataflow | pass / pass / pass / pass | `be7c293dfd569a8d` |
| `tool-runtime.json` | workflow (v2) | pass / pass / pass / pass | `6725ce713ef1f39d` |
| `agent-loop.json` | lifecycle (v3) | pass / pass / pass / pass | `544908442a5d0678` |
| `sub-agent-flow.json` | sequence | pass / pass / pass / pass | `7ad8098629182012` |

All eight were finalized with `--quality showcase --repo-root <clone at the pinned commit>`.
The browser gate used headless Chrome.

## Regenerate

```bash
npx skills add tt-a1i/archify -g
git clone https://github.com/bytedance/deer-flow /tmp/deer-flow
git -C /tmp/deer-flow checkout cf8c29c6b6920671075bd9010b94d89df2a273a8
export ARCHIFY_CHROME=/usr/bin/google-chrome
node ~/.agents/skills/archify/bin/archify.mjs finalize architecture \
  assets/deer-flow/archify/architecture.json diagrams/deer-flow/architecture.html \
  --repo-root /tmp/deer-flow --quality showcase --json
```

Run the command from the repository root, because `meta.output` paths are relative to it.
The first argument is the Archify type (`architecture`, `sequence`, `dataflow`, `workflow`, `lifecycle`),
not the file name. Delivery writes `*.delivery.json` provenance sidecars next to each HTML file.
They hold absolute local paths, so they are git-ignored.
