# Repo Research

A source-code-level research lab for studying important AI infrastructure and agent repositories.

This repository is not a collection of README summaries. Each study should trace real implementation paths, identify reusable architectural ideas, produce source-grounded diagrams, and build a minimal working reproduction of the core design.

## Studies

| Repository | Pinned commit | Research | Diagrams | Experiment |
|---|---|---|---|---|
| [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) | `16e84d9` (SDK 0.7.22) | [research/deepagents.md](research/deepagents.md) · [web](https://woaitqs.github.io/repo-research/research/deepagents.html) | [8 Archify diagrams](diagrams/deepagents/) | [experiments/deepagents](experiments/deepagents/) |
| [letta-ai/letta-code](https://github.com/letta-ai/letta-code) | `4b028fa` (v0.34.4) | [research/letta-code.md](research/letta-code.md) · [web](https://woaitqs.github.io/repo-research/research/letta-code.html) | [8 Archify diagrams](diagrams/letta-code/) | [experiments/letta-code](experiments/letta-code/) |

## What each repository study should contain

For every target repository:

1. Understand the complete architecture.
2. Trace the main execution flow from real entry points.
3. Identify the core abstractions and their responsibilities.
4. Explain context, memory, tools, agents, runtime, and orchestration where applicable.
5. Produce architecture / workflow / sequence diagrams with [Archify](https://github.com/tt-a1i/archify).
6. Extract at least five implementation decisions worth learning from.
7. Build a minimal reproduction under `experiments/<project-name>/`.
8. Run and verify the reproduction.
9. Write the final study under `research/<project-name>.md`.
10. Expose the study and interactive diagrams through the GitHub Pages site.

## How research is performed

1. **Pin the target.** Clone it into a temporary directory, never into this repo. Record `git rev-parse HEAD` and cite every source reference against that commit.
2. **Map, then trace.** Read the manifests, entry points (console scripts, servers) and the composition root. Then follow real call sites end to end. Read the dependency source too when the target delegates to a framework. For deepagents that meant reading LangChain's `create_agent`.
3. **Separate fact from interpretation.** Facts are backed by `path:line`, a test, or observed runtime behavior. Interpretations are labelled as such, and open questions stay explicit.
4. **Verify claims at runtime when it is cheap.** For example, drive the real library with a scripted fake model. The deepagents probe found two behaviors that were not obvious from reading alone. When the target is a CLI, run its published build against a real model through a small logging proxy, so every request body is recorded. The letta-code study used this to check what actually entered the model's context, and to measure prompt-cache hits per call.
5. **Run the target's own tests** with its locked dependencies, so environment noise is not mistaken for findings. Trace every failure to a cause and re-run it with that cause removed before reporting it.
6. **Reproduce the architecture, not the product.** Build a small dependency-light version, test its architectural invariants, and mutation-test the tests.
7. **Diagram from evidence.** Every Archify node carries `sources` pinned to the commit.

## Repository layout

```text
repo-research/
├── README.md
├── index.md                     # homepage (repository table)
├── _config.yml                  # GitHub Pages (Jekyll, minima)
├── _layouts/
│   └── research.html            # article layout: wider column + Mermaid rendering
├── research/
│   └── <project-name>.md        # the study (front matter: layout: research)
├── diagrams/
│   └── <project-name>/
│       ├── architecture.html    # Archify output (self-contained HTML)
│       ├── execution-flow.html
│       └── ...
├── assets/
│   └── <project-name>/
│       └── archify/*.json       # Archify candidates (diagram sources) + provenance README
└── experiments/
    └── <project-name>/
        ├── README.md
        ├── run.sh               # install -> build -> test -> run
        ├── src/
        └── tests/
```

## Adding a new study

1. Create `research/<project-name>.md` with this front matter:
   ```yaml
   ---
   layout: research
   title: "<project> — source-level study"
   permalink: /research/<project-name>.html
   ---
   ```
   Follow the section structure of `research/deepagents.md`.
2. Put Archify candidates in `assets/<project-name>/archify/` and finalize them into `diagrams/<project-name>/` (see [Archify](#archify)). At minimum create `architecture`, `execution-flow`, `core-abstractions` and `context-flow`.
3. Put the reproduction in `experiments/<project-name>/`, with `README.md`, `src/`, `tests/` and `run.sh`.
4. Add a row to the table in `index.md` and to the [Studies](#studies) table above.
5. Avoid Liquid template delimiters (double curly braces, or a curly brace followed by a percent sign) anywhere in published Markdown, this README included. GitHub Pages evaluates them and the build fails.

## Reading

GitHub Pages site:

**https://woaitqs.github.io/repo-research/**

Each repository should get its own research page and its own diagram directory, for example:

- `/research/deepagents.html`
- `/diagrams/deepagents/architecture.html`
- `/diagrams/deepagents/execution-flow.html`

> GitHub Pages must be enabled once in **Settings → Pages → Deploy from a branch → main / (root)**.

Mermaid code fences in research articles render natively on GitHub. On Pages, `_layouts/research.html` converts them in the browser using Mermaid from jsDelivr.

### Local preview

```bash
cat > /tmp/Gemfile <<'EOF'
source "https://rubygems.org"
gem "github-pages", group: :jekyll_plugins
gem "webrick"
EOF
BUNDLE_GEMFILE=/tmp/Gemfile bundle install
BUNDLE_GEMFILE=/tmp/Gemfile bundle exec jekyll serve   # http://127.0.0.1:4000/
```

## Archify

Install the Archify skill:

```bash
npx skills add tt-a1i/archify -g
```

Use Archify for source-grounded architecture, workflow, sequence, data-flow, and lifecycle diagrams. Prefer validated, revision-pinned diagrams when source evidence is available.

How it is used here:

- Pick the diagram type by question:
  - `architecture`: components and boundaries;
  - `sequence`: request lifecycle or delegation;
  - `dataflow`: what enters context, and memory;
  - `workflow` (v2): tool pipeline with an approval lane;
  - `lifecycle` (v2): the agent loop as states.
- Pin `meta.repository` to the studied commit, and give each node 1–3 `sources` (`path`, `line`, `end_line`). The rendered `SRC n` badges link to the exact GitHub lines.
- Finalize from the repository root. `finalize` runs schema validation, verified delivery, a strict provenance check and a headless-browser check:

  ```bash
  export ARCHIFY_CHROME=/path/to/chrome
  node ~/.agents/skills/archify/bin/archify.mjs finalize <type> \
    assets/<project>/archify/<name>.json diagrams/<project>/<name>.html \
    --repo-root /path/to/target/clone --quality showcase --json
  ```

- Optional perceptual review: `archify.mjs visual-check diagrams/<project>/<name>.html --summary --require-provenance` captures screenshots. Look at them before claiming visual quality.
- `*.delivery.json`, `*.finalize.json`, `*.finalize-summary.json` and `*.browser-check.json` receipts contain absolute local paths and are git-ignored. Delete a diagram's old `*.browser-check.json` before re-finalizing it.

## Running experiments

Each experiment is self-contained:

```bash
cd experiments/<project-name>
./run.sh          # creates .venv, installs, builds, runs tests, runs the demo
```

The deepagents experiment is stdlib-only. `./run.sh demo` runs the narrated demo without installing anything. Its `upstream_probe/` directory re-checks the research claims against the real SDK.

The letta-code experiment (`minilc`) is also stdlib-only Python and needs `git` on `PATH`. Its extra checks need the target's toolchain:

- `./run.sh probe <letta-code clone>` runs a TypeScript probe against the real `LocalBackend` with Bun. It needs no network.
- `real_model/run_letta_real.py` drives the built `letta.js` with Node against an OpenAI-compatible model. It reads the API key from `ARK_API_KEY`; never commit keys.
- `python3 mutation_check.py` injects architectural regressions and checks that the tests catch each one.

## Research principle

Every important claim should be traceable to actual source code, documentation, tests, configuration, or verified runtime behavior.

Do not infer architecture from README marketing language alone.
