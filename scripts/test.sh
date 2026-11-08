#!/usr/bin/env bash
#
# Run the backend test suite and the frontend unit tests.
# Frontend tests are skipped when Node.js is unavailable.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "error: .venv is missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

echo "== Backend (pytest) =="
(
  cd backend
  "$ROOT/.venv/bin/python" -m pytest -q
)

if command -v node >/dev/null 2>&1; then
  echo
  echo "== Frontend (node --test) =="
  node --test frontend/js/tests/format.test.mjs
else
  echo
  echo "Node.js not found; skipping frontend tests."
fi
