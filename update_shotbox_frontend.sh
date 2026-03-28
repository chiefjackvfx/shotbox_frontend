#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
WAIT_PID="${1:-}"
BRANCH="main"

cd "$REPO_DIR"

if [[ -n "$WAIT_PID" ]]; then
  while kill -0 "$WAIT_PID" 2>/dev/null; do
    sleep 1
  done
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Git is not installed or not on PATH."
  exit 1
fi

if [[ ! -d ".git" ]]; then
  echo "This folder is not a git checkout."
  exit 1
fi

CURRENT_BRANCH="$(git branch --show-current 2>/dev/null || true)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  echo "Self-update only supports the '$BRANCH' branch. Current branch: ${CURRENT_BRANCH:-unknown}."
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Local changes detected. Commit or discard them before updating."
  exit 1
fi

git pull --ff-only origin "$BRANCH"

if [[ -x "$REPO_DIR/venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_DIR/venv/bin/python"
elif [[ -x "$REPO_DIR/../.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_DIR/../.venv/bin/python"
else
  echo "No supported virtual environment was found."
  exit 1
fi

"$PYTHON_BIN" -m pip install -r "$REPO_DIR/requirements.txt"
nohup "$PYTHON_BIN" "$REPO_DIR/main.py" >/dev/null 2>&1 &
