#!/usr/bin/env bash
#
# Install Python dependencies and prepare the local database.
# Idempotent: safe to run again after pulling changes.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"

echo "Adreoz Server Manager — setup"
echo "Project: $ROOT"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON was not found. Install Python 3.11 or newer." >&2
  exit 1
fi

mkdir -p data/servers data/backups data/logs
chmod 700 data data/servers data/backups data/logs
if [[ -f data/asm.db ]]; then
  chmod 600 data/asm.db
fi

if [[ ! -x .venv/bin/python ]]; then
  if command -v uv >/dev/null 2>&1; then
    echo "Creating .venv with uv…"
    uv venv .venv
  else
    echo "Creating .venv with venv…"
    "$PYTHON" -m venv .venv
  fi
fi

if command -v uv >/dev/null 2>&1; then
  echo "Installing dependencies with uv…"
  uv pip install --python .venv/bin/python -r backend/requirements-dev.txt
else
  echo "Installing dependencies with pip…"
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r backend/requirements-dev.txt
fi

echo "Applying database migrations…"
(
  cd backend
  "$ROOT/.venv/bin/python" -m alembic upgrade head
)
if [[ -f data/asm.db ]]; then
  chmod 600 data/asm.db
fi

cat <<'EOF'

Setup complete.

Next steps:
  1. Optional: copy .env.example to .env and adjust the configuration.
  2. Start the panel: ./scripts/run.sh
  3. Open http://127.0.0.1:8080 and complete the first-run administrator wizard.

The panel binds to loopback by default. Read docs/security.md before exposing it.
EOF
