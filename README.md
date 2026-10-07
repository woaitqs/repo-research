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

## Repository layout

```text
repo-research/
├── README.md
├── index.md
├── _config.yml
├── research/
│   └── <project-name>.md
├── diagrams/
│   └── <project-name>/
│       ├── architecture.html
│       ├── execution-flow.html
│       └── ...
└── experiments/
    └── <project-name>/
        ├── README.md
        ├── src/
        └── tests/
```

## Reading

GitHub Pages site:

**https://woaitqs.github.io/repo-research/**

Each repository should get its own research page and its own diagram directory, for example:

- `/research/deepagents.html`
- `/diagrams/deepagents/architecture.html`
- `/diagrams/deepagents/execution-flow.html`

> GitHub Pages must be enabled once in **Settings → Pages → Deploy from a branch → main / (root)**.

## Archify

Install the Archify skill:

```bash
npx skills add tt-a1i/archify -g
```

Use Archify for source-grounded architecture, workflow, sequence, data-flow, and lifecycle diagrams. Prefer validated, revision-pinned diagrams when source evidence is available.

## Research principle

Every important claim should be traceable to actual source code, documentation, tests, configuration, or verified runtime behavior.

Do not infer architecture from README marketing language alone.
