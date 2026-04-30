#!/usr/bin/env bash

set -euo pipefail

REPO_URL="https://github.com/chiefjackvfx/shotbox_frontend.git"
PUBLISHED_BRANCH="main"
LAUNCH_DIR="$(pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STEP="Starting"
FAIL_MSG=""

usage() {
  cat <<'EOF'
Usage: ./mac_install_shotbox_frontend.command [TARGET_FOLDER]

If this script is run inside an existing ShotBox frontend checkout,
it repairs and updates that checkout in place.

If this script is run from a normal folder without a checkout:
  - with no argument, it clones into:
    next to this command file as "shotbox_frontend"
  - with a target argument, it clones into that path instead

Examples:
  ./mac_install_shotbox_frontend.command
  ./mac_install_shotbox_frontend.command "/Applications/ShotBox/shotbox_frontend"
EOF
}

wait_before_exit() {
  if [[ -t 0 ]]; then
    printf 'Press Return to close this window... '
    read -r _ || true
  fi
}

fail() {
  echo
  echo "[Error] Step failed: $STEP"
  echo "[Error] $FAIL_MSG"
  echo
  wait_before_exit
  exit 1
}

resolve_python_cmd() {
  if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_CMD="python3.11"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
  else
    return 1
  fi
}

is_macos() {
  [[ "$(uname -s 2>/dev/null || true)" == "Darwin" ]]
}

prompt_for_command_line_tools() {
  echo "[Info] ShotBox needs Apple's Command Line Tools, not the full Xcode app."
  echo "[Info] macOS should now open an installer prompt."
  echo

  if xcode-select --install >/dev/null 2>&1; then
    echo "[Info] Finish the Command Line Tools install, then run this ShotBox installer again."
  else
    echo "[Info] If no installer prompt appears, run this in Terminal:"
    echo "       xcode-select --install"
    echo "[Info] Then run this ShotBox installer again."
  fi
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || "${1:-}" == "/?" ]]; then
  usage
  exit 0
fi

echo "[ShotBox] Starting frontend installer..."
echo "[ShotBox] Launch folder: \"$LAUNCH_DIR\""
echo "[ShotBox] Script folder: \"$SCRIPT_DIR\""
echo

STEP="Checking Apple Command Line Tools"
echo "[Step] $STEP"
if is_macos; then
  if ! xcode-select -p >/dev/null 2>&1; then
    prompt_for_command_line_tools
    echo
    wait_before_exit
    exit 1
  fi
  echo "[Info] Apple Command Line Tools detected."
else
  echo "[Info] Not running on macOS; skipping Apple Command Line Tools check."
fi
echo

STEP="Checking Git"
echo "[Step] $STEP"
if ! command -v git >/dev/null 2>&1; then
  if is_macos; then
    prompt_for_command_line_tools
    echo
    wait_before_exit
    exit 1
  fi
  FAIL_MSG="Git is not installed or not on PATH."
  fail
fi
echo "[Info] Git detected."
echo

STEP="Checking Python"
echo "[Step] $STEP"
PYTHON_CMD=""
if ! resolve_python_cmd; then
  FAIL_MSG="Could not find a usable Python installation via 'python3.11', 'python3', or 'python'."
  fail
fi
echo "[Info] Selected Python command: $PYTHON_CMD"
echo

MODE=""
REPO_DIR=""

if [[ -f "$SCRIPT_DIR/main.py" && -d "$SCRIPT_DIR/.git" ]]; then
  MODE="existing checkout"
  REPO_DIR="$SCRIPT_DIR"
fi

if [[ -z "$REPO_DIR" ]]; then
  if [[ -n "${1:-}" ]]; then
    REPO_DIR="$1"
  else
    REPO_DIR="$SCRIPT_DIR/shotbox_frontend"
  fi

  MODE="standalone bootstrap"
  if [[ -d "$REPO_DIR/.git" ]]; then
    MODE="existing checkout"
  fi
fi

if [[ -z "$REPO_DIR" ]]; then
  STEP="Resolving install target"
  FAIL_MSG="Could not determine the frontend repo target folder."
  fail
fi

echo "[Info] Installer mode: $MODE"
echo "[Info] Repo target: \"$REPO_DIR\""
echo

if [[ "$MODE" == "standalone bootstrap" ]]; then
  STEP="Preparing bootstrap target"
  echo "[Step] $STEP"

  if [[ -d "$REPO_DIR/.git" ]]; then
    echo "[Info] Existing git checkout found at \"$REPO_DIR\"."
    echo
  else
    if [[ -e "$REPO_DIR" && ! -d "$REPO_DIR" ]]; then
      FAIL_MSG="Target path exists and is not a folder: $REPO_DIR"
      fail
    fi

    if [[ -d "$REPO_DIR" && -n "$(find "$REPO_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
      FAIL_MSG="Target folder exists and is not an empty git checkout: $REPO_DIR"
      fail
    fi

    REPO_PARENT="$(dirname "$REPO_DIR")"
    if ! mkdir -p "$REPO_PARENT"; then
      FAIL_MSG="Could not create parent folder: $REPO_PARENT"
      fail
    fi

    echo "[Info] Bootstrap target is ready."
    echo

    STEP="Cloning frontend repo"
    echo "[Step] $STEP"
    if ! git clone "$REPO_URL" "$REPO_DIR"; then
      FAIL_MSG="Git clone failed. Check network access and GitHub permissions."
      fail
    fi
    echo "[Info] Clone completed."
    echo
  fi
fi

STEP="Opening repo"
echo "[Step] $STEP"
if ! cd "$REPO_DIR"; then
  FAIL_MSG="Could not change into repo target: $REPO_DIR"
  fail
fi
echo "[Info] Working in \"$REPO_DIR\"."
echo

STEP="Checking published branch"
echo "[Step] $STEP"
CURRENT_BRANCH="$(git branch --show-current 2>/dev/null || true)"
if [[ -z "$CURRENT_BRANCH" ]]; then
  FAIL_MSG="Could not determine the current git branch."
  fail
fi

if [[ "$CURRENT_BRANCH" != "$PUBLISHED_BRANCH" ]]; then
  FAIL_MSG="Expected the published checkout to be on '$PUBLISHED_BRANCH'. Current branch: $CURRENT_BRANCH"
  fail
fi

echo "[Info] Current branch: $CURRENT_BRANCH"
echo

STEP="Checking working tree"
echo "[Step] $STEP"
GIT_STATUS="$(git status --porcelain)" || {
  FAIL_MSG="Could not check git working tree status."
  fail
}
if [[ -n "$GIT_STATUS" ]]; then
  FAIL_MSG="Local changes detected in $REPO_DIR. Commit or discard them before install or repair."
  fail
fi
echo "[Info] Working tree is clean."
echo

STEP="Pulling latest frontend code"
echo "[Step] $STEP"
if ! git pull --ff-only origin "$PUBLISHED_BRANCH"; then
  FAIL_MSG="Git pull failed. Resolve the repository state manually and retry."
  fail
fi
echo "[Info] Repo is up to date on origin/$PUBLISHED_BRANCH."
echo

STEP="Preparing virtual environment"
echo "[Step] $STEP"
if [[ ! -x "$REPO_DIR/venv/bin/python" ]]; then
  echo "[Info] Creating virtual environment at \"$REPO_DIR/venv\""
  if ! "$PYTHON_CMD" -m venv "$REPO_DIR/venv"; then
    FAIL_MSG="Virtual environment creation failed."
    fail
  fi
else
  echo "[Info] Reusing existing virtual environment at \"$REPO_DIR/venv\""
fi
echo

STEP="Installing Python requirements"
echo "[Step] $STEP"
if ! "$REPO_DIR/venv/bin/python" -m pip install -r "$REPO_DIR/requirements.txt"; then
  FAIL_MSG="Requirements install failed. Review the pip output above."
  fail
fi
echo "[Info] Requirements install completed."
echo

STEP="Final run prompt"
echo "[Step] $STEP"
echo "[Info] Run script: \"$REPO_DIR/run_shotbox_frontend.sh\""
printf 'Launch ShotBox now? [Y/N]: '
read -r RUN_NOW || RUN_NOW=""

case "$RUN_NOW" in
  [Yy])
    if ! bash "$REPO_DIR/run_shotbox_frontend.sh"; then
      FAIL_MSG="ShotBox failed to launch from run_shotbox_frontend.sh."
      fail
    fi
    ;;
  *)
    echo "[Info] Installation completed. Launch later with:"
    echo "       \"$REPO_DIR/run_shotbox_frontend.sh\""
    ;;
esac

echo
echo "[Success] ShotBox frontend install or repair completed successfully."
exit 0
