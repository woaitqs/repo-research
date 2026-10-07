# Archify sources for the openai-agents-python study

These JSON files are the authored Archify candidates behind
[`diagrams/openai-agents-python/`](../../../diagrams/openai-agents-python/). Every node carries
`sources` pinned to `openai/openai-agents-python@71c2da4de47159ccc37905b8fe781be805dbfa66`.
In the rendered HTML, each node's `SRC n` badge links to the exact GitHub lines.

| Diagram | Archify type | Gates (validate / deliver / strict check / browser) | Artifact sha256 (prefix) |
|---|---|---|---|
| `architecture.json` | architecture | pass / pass / pass / pass | `3bd01c0f1b79be6c` |
| `execution-flow.json` | sequence | pass / pass / pass / pass | `0610403d4cd24c0b` |
| `core-abstractions.json` | architecture | pass / pass / pass / pass | `b9c9e5670bed294b` |
| `context-flow.json` | dataflow | pass / pass / pass / pass | `f07d530c8c038993` |
| `memory-flow.json` | dataflow | pass / pass / pass / pass | `4e886e7329c6308d` |
| `tool-runtime.json` | workflow (v2) | pass / pass / pass / pass | `7036da8556ce926c` |
| `agent-loop.json` | lifecycle (v2) | pass / pass / pass / pass | `c31d990fedaff16d` |
| `sub-agent-flow.json` | sequence | pass / pass / pass / pass | `ac49601e7c7947d4` |

All eight were finalized with `--quality showcase --repo-root <clone at the pinned commit>`
(Archify 3.0.1). The browser gate used headless Chromium. `visual-check` captured every
diagram (light and dark, 1440×900 and 2048×1320); the light 1440×900 captures were inspected
by eye. Changes made after that review:

- `agent-loop`: the first version had a separate "switch agent" state and five crossing routes.
  The handoff is now folded into the `RunAgain / Handoff` return edge, and `interrupted` sits
  directly under `side effects` so `resume` is a straight line.
- `tool-runtime`: the `capability tool` edge took a detour around the lanes; it is now a straight
  `drop` route.

Known cosmetic issue: in `sub-agent-flow`, the `Agent.as_tool` segment label renders slightly
above its band (the renderer moves it away from the first message label).

Authoring constraints worth knowing for the next study (learned the hard way here):
`connections`/`messages` cannot carry `sources` (only nodes and participants can, max 3 each);
a showcase canvas must be either ≥ 1.55 wide-ratio or short enough for 900 px; and in dataflow
diagrams a second edge on the same side of a node offsets the port by 7 px, which fails the
8 px micro-segment rule unless the straight neighbour gets a matching `yOffset`.

## Regenerate

```bash
npx skills add tt-a1i/archify -g          # installs the skill (bin/archify.mjs)
git clone https://github.com/openai/openai-agents-python /tmp/oaa
git -C /tmp/oaa checkout 71c2da4de47159ccc37905b8fe781be805dbfa66
export ARCHIFY_CHROME=/path/to/chrome      # needed for the browser gate
node ~/.agents/skills/archify/bin/archify.mjs finalize architecture \
  assets/openai-agents-python/archify/architecture.json diagrams/openai-agents-python/architecture.html \
  --repo-root /tmp/oaa --quality showcase --json
```

Run the command from the repository root, because `meta.output` paths are relative to it.
Delivery and browser-check write `*.delivery.json`, `*.finalize*.json` and
`*.browser-check.json` sidecars next to each HTML file. They hold absolute local paths and are
git-ignored.
