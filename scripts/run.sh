#!/usr/bin/env bash
#
# Start Adreoz Server Manager. Migrations are applied automatically by
# `python -m app`, so this is the only command needed for normal use.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "error: .venv is missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

cd backend
exec "$ROOT/.venv/bin/python" -m app "$@"
