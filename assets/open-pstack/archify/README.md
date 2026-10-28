# Archify sources for the open-pstack study

These JSON files are the authored Archify candidates behind
[`diagrams/open-pstack/`](../../../diagrams/open-pstack/). Every node carries `sources`
pinned to `ericlitman/open-pstack@b4c5e3d63318308b7e11f26b2bc156e7793d834a`. In the rendered
HTML, each node's `SRC n` badge links to the exact GitHub lines.

| Diagram | Archify type | Gates (validate / deliver / strict check / browser) | Artifact sha256 (prefix) |
|---|---|---|---|
| `architecture.json` | architecture | pass / pass / pass / pass | `bb9c3eeee0ab833e` |
| `execution-flow.json` | sequence | pass / pass / pass / pass | `76ce06217befb8e1` |
| `core-abstractions.json` | architecture | pass / pass / pass / pass | `6b452d7eeae911d2` |
| `context-flow.json` | dataflow | pass / pass / pass / pass | `757935e5347c6dea` |
| `memory-flow.json` | dataflow | pass / pass / pass / pass | `73ef6e21d7ef7d99` |
| `tool-runtime.json` | workflow (v2) | pass / pass / pass / pass | `79e73289f03292b0` |
| `agent-loop.json` | lifecycle (v2) | pass / pass / pass / pass | `99a525b17a6bdde0` |
| `sub-agent-flow.json` | sequence | pass / pass / pass / pass | `7e26bba6867d38cf` |

All eight were finalized with Archify 3.0.1, `--quality showcase --repo-root <clone at the pinned commit>`.
The browser gate used headless Chrome. Screenshots of every diagram (light theme,
1440×900, plus dark for the two that were repaired last) were captured with
`visual-check` and inspected by eye.

Repair notes, recorded so later edits don't reintroduce the same failures:

- **Sequence:** a participant's label must fit its box (about 113px at width 1080). "pstack-runner" fits; longer names were moved to the sublabel.
- **Workflow (v2):** columns run 0..5 and the viewBox width must stay ≤ 1023. Node labels have room for about 12 monospace characters, so the tool-runtime nodes keep the exit codes in the sublabel ("Usage error" / "64 · none reserved").
- **Lifecycle (v2):** columns run 0..4. The agent loop puts the main path on row 0, detours on row 1 and terminal states on row 2.
- **Dataflow:** a `via` corner must line up with both of its neighbours (no diagonal segments), and the viewBox needs a width-to-height ratio of at least 1.55, so memory-flow is 1080×696.
- **Dataflow legend:** the auto legend calls dashed flows "async batch". Both dataflows override that label, because here dashed means "read later" or "read only when needed". Keep the override short; a long label runs into the next legend swatch.

## Regenerate

```bash
npx skills add tt-a1i/archify -g          # installs the skill (bin/archify.mjs)
git clone https://github.com/ericlitman/open-pstack /tmp/open-pstack
git -C /tmp/open-pstack checkout b4c5e3d63318308b7e11f26b2bc156e7793d834a
export ARCHIFY_CHROME=/path/to/chrome      # needed for the browser gate
node ~/.agents/skills/archify/bin/archify.mjs finalize architecture \
  assets/open-pstack/archify/architecture.json diagrams/open-pstack/architecture.html \
  --repo-root /tmp/open-pstack --quality showcase --json
```

Run the command from the repository root, because `meta.output` paths are relative to it.
Delivery writes `*.delivery.json`, `*.finalize*.json` and `*.browser-check.json` receipts
next to each HTML file. They hold absolute local paths, so they are git-ignored. Delete a
diagram's old `*.browser-check.json` before re-finalizing it; the browser gate refuses to
overwrite evidence it does not own.
