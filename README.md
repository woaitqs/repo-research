# Repo Research

A source-code-level research lab for studying important AI infrastructure and agent repositories.

This repository is not a collection of README summaries. Each study should trace real implementation paths, identify reusable architectural ideas, produce source-grounded diagrams, and build a minimal working reproduction of the core design.

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

## Studies

| Repository | Article | Diagrams | Reproduction |
|---|---|---|---|
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) (+ [software-agent-sdk](https://github.com/OpenHands/software-agent-sdk)) | [research/openhands.md](research/openhands.md) | [diagrams/openhands/](diagrams/openhands/) | [experiments/openhands/](experiments/openhands/) |

## Repository layout

```text
repo-research/
├── README.md
├── index.md                    # site home page with the research library table
├── _config.yml                 # Jekyll (GitHub Pages) config; experiments/ is excluded from the site
├── _layouts/
│   └── research.html           # article layout: breadcrumb + client-side Mermaid rendering
├── research/
│   └── <project-name>.md       # the article (front matter: layout: research)
├── diagrams/
│   └── <project-name>/
│       ├── index.md            # diagram gallery page
│       ├── architecture.html   # Archify output (standalone HTML, served as-is)
│       ├── execution-flow.html
│       ├── ...
│       └── specs/*.json        # Archify source specs (re-renderable)
├── experiments/
│   └── <project-name>/
│       ├── README.md
│       ├── run.sh              # install + build + test + demo
│       ├── src/
│       └── tests/
└── assets/
    └── <project-name>/         # evidence: probe reports, run logs, Archify receipts
```

## Reading

GitHub Pages site:

**https://woaitqs.github.io/repo-research/**

Each repository should get its own research page and its own diagram directory, for example:

- `/research/openhands.html`
- `/diagrams/openhands/` (gallery)
- `/diagrams/openhands/architecture.html`
- `/diagrams/openhands/execution-flow.html`

> GitHub Pages must be enabled once in **Settings → Pages → Deploy from a branch → main / (root)**.

Preview the site locally (Ruby + Bundler):

```bash
cat > /tmp/Gemfile <<'EOF'
source "https://rubygems.org"
gem "github-pages", group: :jekyll_plugins
gem "webrick"
EOF
BUNDLE_GEMFILE=/tmp/Gemfile bundle install
BUNDLE_GEMFILE=/tmp/Gemfile bundle exec jekyll serve --source . --destination /tmp/repo-research-site
```

Mermaid code blocks render natively on GitHub; on the Pages site the `research` layout renders them
client-side with Mermaid from jsDelivr.

## How a study is performed

1. Clone the target (and any repository it depends on for core behaviour) into a temporary
   directory, never into this repo. Pin the exact commits you study.
2. Read entry points, follow call sites, and keep `path:line` evidence for every claim. Use the
   target's own tests and, where possible, run the real code (a small probe script) to observe
   runtime behaviour; mark anything not verified as **uncertain**.
3. Write the reproduction in `experiments/<project-name>/`; it must install, build, test and run
   from `./run.sh`.
4. Produce Archify diagrams from source evidence (below) and Mermaid diagrams in the article.
5. Write `research/<project-name>.md`, store evidence under `assets/<project-name>/`, add a row to
   `index.md` and to the Studies table above.

## How to add a new study

```text
research/<project-name>.md            front matter: layout: research, title, diagrams, experiment
diagrams/<project-name>/index.md      gallery page linking every diagram
diagrams/<project-name>/specs/*.json  Archify specs
diagrams/<project-name>/*.html        finalized Archify output
experiments/<project-name>/           README.md, run.sh, src/, tests/
assets/<project-name>/                probe outputs, logs, Archify receipts
index.md                              new row in the Research Library table
```

Never commit API keys: experiments read keys from environment variables and `.env` is git-ignored.

## Archify

Install the Archify skill:

```bash
npx skills add tt-a1i/archify -g
```

Use Archify for source-grounded architecture, workflow, sequence, data-flow, and lifecycle diagrams. Prefer validated, revision-pinned diagrams when source evidence is available.

Workflow used for the studies here:

1. Write a JSON spec (`diagrams/<project-name>/specs/<name>.json`) with `meta.repository`
   (credential-free origin URL + 40-char revision) and `sources` (`path`, `line`, `end_line`) on
   every repository-backed node. This produces `SRC` badges that link to GitHub at that revision.
2. Run from a scratch working directory so receipts and provenance sidecars (which contain absolute
   paths) stay out of this repo:

   ```bash
   export ARCHIFY_CHROME=/path/to/chromium      # needed for the browser gate
   node ~/.agents/skills/archify/bin/archify.mjs finalize <type> \
     /path/to/repo-research/diagrams/<project>/specs/<name>.json \
     diagrams/<project>/<name>.html \
     --repo-root /path/to/pinned/target/checkout --quality showcase --json
   node ~/.agents/skills/archify/bin/archify.mjs visual-check diagrams/<project>/<name>.html \
     --summary --require-provenance --out-dir diagrams/<project>/<name>.visual-check
   ```

3. Copy the finalized HTML into `diagrams/<project-name>/` and record the receipt summary
   (gates, SHA-256, verified reference count) in `assets/<project-name>/archify-receipts.json`.

## Running experiments

Each experiment is self-contained:

```bash
cd experiments/<project-name>
./run.sh            # creates .venv, installs, builds, runs tests and an offline demo
./run.sh --live     # optional: also runs against a real model (keys from the environment)
```

See each experiment's README for the mapping between reproduction modules and upstream source files.

## Research principle

Every important claim should be traceable to actual source code, documentation, tests, configuration, or verified runtime behavior.

Do not infer architecture from README marketing language alone.
