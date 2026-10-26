#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing Shotbox environment. Run the Shotbox installer or sh setup_runtime.sh first." >&2
  exit 1
fi

cd "$SCRIPT_DIR"
exec "$PYTHON_BIN" main.py
