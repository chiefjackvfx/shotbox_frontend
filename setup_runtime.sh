#!/bin/sh
# Frontend-owned Python provisioning; system Python and shell profiles stay intact.
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
version=$(cat "$project/runtime_python.txt")
if [ "$(uname -s)" = Linux ] && [ -f "$project/runtime_python_linux.txt" ]; then
    version=$(cat "$project/runtime_python_linux.txt")
fi
for argument in "$@"; do
    if [ "$argument" = '--qualify-python313' ]; then version=3.13; fi
done
case "$version" in
    3.12|3.13) ;;
    *) echo 'Unsupported Shotbox Python version.' >&2; exit 1 ;;
esac
tools="$project/.runtime/tools"
uv="$tools/bin/uv"
downloads="$project/.cache/temp/runtime-downloads"
export UV_UNMANAGED_INSTALL="$tools/bin" UV_NO_MODIFY_PATH=1 UV_NO_CONFIG=1
export UV_PYTHON_INSTALL_DIR="$tools/python" UV_CACHE_DIR="$downloads/uv"
export PYTHONUTF8=1 PYTHON_JIT=0 TMPDIR="$downloads" TEMP="$downloads" TMP="$downloads"
unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
mkdir -p "$tools/bin" "$downloads" "$project/.cache/temp/.active-tasks"
lease="$project/.cache/temp/.active-tasks/runtime-bootstrap-$$.json"
printf '{"pid":%s}\n' "$$" > "$lease"
trap 'rm -f "$lease"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
if [ ! -x "$uv" ]; then
    installer="$downloads/install-uv.sh"
    echo '[Shotbox] Downloading the local setup tool...'
    if command -v curl >/dev/null 2>&1; then
        curl -fLsS https://astral.sh/uv/0.12.21/install.sh -o "$installer"
    elif command -v wget >/dev/null 2>&1; then
        wget -q https://astral.sh/uv/0.12.21/install.sh -O "$installer"
    else
        echo 'Install curl or wget, then retry the Shotbox installer.' >&2
        exit 1
    fi
    sh "$installer"
fi
echo "[Shotbox] Preparing local Python $version..."
"$uv" python install "$version" --no-bin --no-registry
python=$("$uv" python find --managed-python --no-project "$version")
"$python" "$project/runtime_setup.py" --uv "$uv" "$@"
