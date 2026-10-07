#!/usr/bin/env bash
# Install, build, test and run the miniagents reproduction in an isolated venv.
#   ./run.sh            full pipeline (install -> build -> tests -> demo)
#   ./run.sh demo       demo only (no install; uses ./src directly)
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "demo" ]]; then
  PYTHONPATH=src exec python3 -m miniagents.demo
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
"$VENV/bin/python" -m pytest -q -p no:cacheprovider

echo "==> [4/4] demo"
"$VENV/bin/miniagents-demo"
