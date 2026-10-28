#!/usr/bin/env bash
# Install, build, test and run the minipstack reproduction in an isolated venv.
#   ./run.sh            full pipeline (install -> build -> tests -> demo)
#   ./run.sh demo       demo only (no install; uses ./src directly)
#   ./run.sh probe <open-pstack-clone>   probe the real pstack-runner with scripted CLIs (needs bun and git)
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "demo" ]]; then
  PYTHONPATH=src exec python3 -m minipstack.demo
fi

if [[ "${1:-}" == "probe" ]]; then
  CLONE=${2:?usage: ./run.sh probe /path/to/open-pstack (commit b4c5e3d)}
  exec python3 -I upstream_probe/probe_runner.py "$CLONE" --out upstream_probe/probe_output.json
fi

VENV=${VENV:-.venv}
echo "==> [1/4] create venv + install (editable) with test extras"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -e ".[test]"

echo "==> [2/4] build wheel"
"$VENV/bin/python" -m pip wheel --quiet --no-deps --wheel-dir dist .
ls dist

echo "==> [3/4] tests"
"$VENV/bin/python" -m pytest -q

echo "==> [4/4] demo"
"$VENV/bin/minipstack-demo"
