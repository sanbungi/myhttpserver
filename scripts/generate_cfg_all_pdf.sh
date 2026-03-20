#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] Python not found at: $PYTHON_BIN" >&2
  exit 2
fi

exec "$PYTHON_BIN" "$ROOT_DIR/scripts/generate_cfg.py" src --format pdf --output-dir dist/cfg "$@"
