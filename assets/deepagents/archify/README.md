# Archify sources for the deepagents study

These JSON files are the authored Archify candidates behind
[`diagrams/deepagents/`](../../../diagrams/deepagents/). Every node carries `sources`
pinned to `langchain-ai/deepagents@16e84d927e7e13c41a10c071380c875af6a562f5`. In the
rendered HTML, each node's `SRC n` badge links to the exact GitHub lines.

| Diagram | Archify type | Gates (validate / deliver / strict check / browser) | Artifact sha256 (prefix) |
|---|---|---|---|
| `architecture.json` | architecture | pass / pass / pass / pass | `bdcb8213d2f7f8f0` |
| `execution-flow.json` | sequence | pass / pass / pass / pass | `4c5a4430fdcb1dc1` |
| `core-abstractions.json` | architecture | pass / pass / pass / pass | `b747655c94887de7` |
| `context-flow.json` | dataflow | pass / pass / pass / pass | `30f37e59b9cbfb19` |
| `memory-flow.json` | dataflow | pass / pass / pass / pass | `cd9f9fe5f79d55fa` |
| `tool-runtime.json` | workflow (v2) | pass / pass / pass / pass | `26df562b302046eb` |
| `agent-loop.json` | lifecycle (v2) | pass / pass / pass / pass | `29f44e149bbea52a` |
| `sub-agent-flow.json` | sequence | pass / pass / pass / pass | `db0a9351174be35b` |

All eight were finalized with `--quality showcase --repo-root <clone at the pinned commit>`.
The browser gate used headless Chromium. Screenshots of every diagram (light theme,
1440×900) were also captured with `visual-check` and inspected by eye.
The only open advisory is in `agent-loop`: the `ToolMessages` return edge takes a 2-bend
detour around the router node, which a loop-back edge needs.

## Regenerate

```bash
npx skills add tt-a1i/archify -g          # installs the skill (bin/archify.mjs)
git clone https://github.com/langchain-ai/deepagents /tmp/deepagents
git -C /tmp/deepagents checkout 16e84d927e7e13c41a10c071380c875af6a562f5
export ARCHIFY_CHROME=/path/to/chrome      # needed for the browser gate
node ~/.agents/skills/archify/bin/archify.mjs finalize architecture \
  assets/deepagents/archify/architecture.json diagrams/deepagents/architecture.html \
  --repo-root /tmp/deepagents --quality showcase --json
```

Run the command from the repository root, because `meta.output` paths are relative to it.
Delivery writes `*.delivery.json` provenance sidecars next to each HTML file. They hold
absolute local paths, so they are git-ignored.
