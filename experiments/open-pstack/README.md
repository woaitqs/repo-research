**English** · [中文](README.zh.md)

# minipstack — minimal reproduction of the open-pstack architecture

A ~1,360-line (docstrings included, demo excluded), **stdlib-only** Python reproduction of the
architecture of [`ericlitman/open-pstack`](https://github.com/ericlitman/open-pstack)
(studied at commit `b4c5e3d`, Open Pstack 1.16.0):

> A skill pack has no agent loop of its own. The parent harness reads a short mandate, a list of
> skill names, and skill bodies on demand. When a role needs another vendor's model, the
> **parent** resolves the route once: its own provider runs natively, every other provider runs
> through a **runner** that makes exactly one model call, proves which model answered, and always
> leaves a receipt. A failed lane is a named **dropout**, never a fallback.

It reproduces the architecture, not the skill content. The provider CLIs in tests and the demo
are scripted fakes, so every claim is checked exactly. The same runner also drives the real
`claude` and `codex` CLIs (see `real_model/`).

Research write-up: [`research/open-pstack.md`](../../research/open-pstack.md)

## What maps to what

| minipstack | upstream open-pstack (`b4c5e3d`) | Idea reproduced |
|---|---|---|
| `registry.py` | `runner/registry/models.ts`, `family.ts`, `index.ts` | one typed source; resolution order current → kept predecessor → rolling-alias revision → unregistered; `resolve_lane` refuses same-provider lanes, unnormalized pins, unlisted efforts and unsupported access kinds |
| `adapters.py` | `runner/providers/{claude,codex,grok,devin}.ts`, `shared.ts` | argv per access mode, preflight classification, output parsing, model proof (provider report or pinned argv), environment policy (inherit or allowlist); no policy fields |
| `runner.py` | `runner/run.ts`, `cli.ts`, `types.ts` | exclusive 0600 reservation; identity scrubbing; executable on the child `PATH`; shared-`.git` deny for Claude writers; one preflight retry only after `unauthenticated`; exactly one model call; one absolute deadline; cancellation that signals the child; status precedence and exit codes |
| `render.py` | `runner/registry/render.ts`, `registry.test.ts` | generated catalog and native agent files; drift check naming the write command; text scan for descriptors and versioned model names in hand-written Markdown |
| `dispatch.py` | `references/provider-dispatch.md`, `skills/{interrogate,arena}/SKILL.md` | parent-owned routes; a child assignment with no routing inputs; all lanes start together; nothing is judged before every lane is terminal; only proven lanes count; dropouts recorded, never replaced; agreement = a line raised by 2+ models |
| `sheet.py` | `skills/setup-pstack/SKILL.md`, `references/codex-tools.md` | model sheet; in-memory normalization of versioned Claude pins; writer roles refused on read-only providers; probe every family before writing; snapshot, readback, restore; byte-identical reruns; one import line |
| `orch.py` | `scripts/orch/store.ts`, `playbooks/orchestrate.md` | store outside the session; atomic writes; lock taken over from a dead holder; ledger keyed by PR and head SHA; inbox drained in batches; status derived from the tables |
| `skills.py` | `hooks/`, the skill tree, `poteto-mode/SKILL.md` | the mandate only on startup/clear/compact; names and descriptions at start; bodies on invoke; principle leaves hidden and resolved by path |

Deliberately **not** reproduced: the skill and playbook prose, Codex-specific dispatch
(`spawn_agent`), provider sidecar directories and run-owned configs (Devin, Cursor, agy), the
Grok preflight's exact output grammar, `watch-pr`, `check-plan.mjs`, the frontier computation in
`orch`, the hook's Windows wrapper, and the live gate.

## Run

```bash
./run.sh          # venv -> pip install -e .[test] -> build wheel -> pytest -> demo
./run.sh demo     # demo only, no install (python3 >= 3.10, git on PATH)
python3 mutation_check.py   # inject 21 architectural regressions; each must fail a test
```

## Upstream probe (needs an open-pstack clone at `b4c5e3d`, plus bun and git)

```bash
./run.sh probe /path/to/open-pstack
```

`upstream_probe/probe_runner.py` drives the shipped `pstack-runner` launcher with scripted
`claude`, `codex`, `grok` and `devin` CLIs (`upstream_probe/fake_cli.py`). It needs no network or
login. Its output is in `upstream_probe/probe_output.json`: **20/20 claims held.**

| Probe | Claim | Result |
|---|---|---|
| P1 | a same-provider lane is refused before anything is reserved | held |
| P2 | an allowlist provider gets only allowed variables; identity markers and other secrets are dropped | held |
| P3 | an inherited provider loses only identity markers; a writer also loses `GIT_*` | held |
| P4 | read-only Claude argv: plan mode, project-only settings, strict MCP, no persistence, denied tools | held |
| P5 | a Claude writer in a linked worktree gets the Bash sandbox with `denyWrite` on the shared `.git` | held |
| P6 | a writer whose cwd is inside the shared `.git` is refused before preflight | held |
| P7 | an existing receipt is never overwritten; reserved files are 0600 | held |
| P8 | an unlisted model is a dropout; the model never runs | held |
| P9 | Grok's preflight retries once, 5 s later, only after `unauthenticated` | held |
| P10 | `opus` answered by a Sonnet revision is `malformed-output` | held |
| P11 | `opus` answered by `claude-opus-5-5` is proven by provider report | held |
| P12 | Codex is proven by pinned argv and runs ephemeral with plugins, multi_agent, hooks and memories disabled | held |
| P13 | an auth error after the preflight is `child-failed` | held |
| P14 | `--timeout` is one deadline across preflight and model | held |
| P15 | SIGTERM reaches the child and keeps a `cancelled` receipt and partial stdout | held |
| P16 | an unnormalized Claude pin is refused | held |
| P17 | a read-only-only provider refuses a writer | held |
| P18 | no implicit timeout | held |
| P19 | exit 0 with unparseable output is `malformed-output` | held |
| P20 | only the fixed identity list leaves an inherited environment | held (5 other `CLAUDE*` variables reach Codex) |

## Real CLIs (`real_model/`)

These scripts use the operator's existing `claude` and `codex` logins. They never read
credential files, and their published results replace local paths with placeholders.

```bash
python3 real_model/run_real_runner.py /path/to/open-pstack --runs 3    # results_run{1,2,3}.json
python3 real_model/run_plugin_session.py /path/to/open-pstack          # plugin_sessions.json
python3 real_model/run_explicit_session.py /path/to/open-pstack        # explicit_sessions.json
PYTHONPATH=src python3 real_model/run_mini_real.py --runs 3            # mini_results.json
```

- `run_real_runner.py` drives the **upstream** `pstack-runner` against the real CLIs: model proof
  for Haiku, the `sonnet` alias and Codex Luna; a real dropout (this account's Codex does not serve
  `gpt-6.1-sol`); and what each access mode stops (R1–R9, 3 runs).
- `run_plugin_session.py` loads the pinned plugin into headless Claude Code (`--plugin-dir`,
  `--setting-sources project`). It measures whether the SessionStart mandate routes a task into a
  pstack skill: 3 models × 3 prompts × 3 runs.
- `run_explicit_session.py` invokes `/pstack:poteto-mode` explicitly and records what follows:
  playbook reads, todolist, dispatch-reference reads, delegations (3 Sonnet + 3 Opus sessions).
- `run_mini_real.py` drives **minipstack** against the same CLIs: two `interrogate` panels per run
  review a diff with a planted defect, one from each parent harness, with two real dropouts.

The study's [Verification](../../research/open-pstack.md#verification) section reports the numbers.
