#!/usr/bin/env bash
# Install, build, test and run the mini_openhands reproduction.
#   ./run.sh           -> venv + editable install + pytest + offline scripted demo
#   ./run.sh --live    -> additionally run the demo against a real model
#                         (needs ARK_API_KEY; optional ARK_BASE_URL, ARK_MODEL)
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "== install / build"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[test]"
python -m compileall -q src

echo "== tests"
python -m pytest -q

echo "== scripted demo (offline)"
python -m mini_openhands.demo

if [ "${1:-}" = "--live" ]; then
  echo "== live demo"
  python -m mini_openhands.demo --live
fi
