"""minipstack: a minimal reproduction of the open-pstack architecture (study artifact).

registry  one typed source for families, providers and role defaults; only resolve_lane builds a lane
adapters  per-CLI argv, preflight, parsing and model proof; no policy
runner    one external lane, one receipt, no fallback, one absolute deadline
render    generated catalog and agent files, drift check, text scan
dispatch  parent-owned routes, fan-out with a terminal barrier, dropouts, interrogate synthesis
sheet     the model sheet and a setup that probes before it writes
skills    what a skill pack puts into context, and when
orch      a single-writer program store that outlives the session
"""

__version__ = "0.1.0"
