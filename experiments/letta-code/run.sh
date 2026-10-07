#!/usr/bin/env bash
# Install, build, test and run the minilc reproduction in an isolated venv.
#   ./run.sh            full pipeline (install -> build -> tests -> demo)
#   ./run.sh demo       demo only (no install; uses ./src directly)
#   ./run.sh probe <letta-code-clone>   run the LocalBackend probe against the real upstream code (needs bun)
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "demo" ]]; then
  PYTHONPATH=src exec python3 -m minilc.demo
fi

if [[ "${1:-}" == "probe" ]]; then
  CLONE=${2:?usage: ./run.sh probe /path/to/letta-code (bun install done, commit 4b028fa)}
  cp upstream_probe/probe_letta_code.ts "$CLONE/src/zz-probe-letta-code.ts"
  trap 'rm -f "$CLONE/src/zz-probe-letta-code.ts"' EXIT
  PH=$(mktemp -d)
  (cd "$CLONE" && HOME=$PH LETTA_LOCAL_BACKEND_DIR=$PH/lcb bun run src/zz-probe-letta-code.ts)
  exit $?
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
"$VENV/bin/minilc-demo"
