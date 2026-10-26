#!/bin/sh
set -u
project=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
if [ "$(uname -s)" != "Linux" ]; then
    echo "Automatic Alpha Gen setup requires Linux or Windows with an NVIDIA GPU." >&2
    exit 1
fi

# Reuse a ready local environment without downloading or installing anything.
for directory in .venv .venv-linux; do
    python="$project/$directory/bin/python"
    if [ ! -x "$python" ]; then continue; fi
    if ! "$python" -c 'import sys; sys.exit(not ((3,12) <= sys.version_info[:2] < (3,14)))' 2>/dev/null; then continue; fi
    "$python" "$project/launcher.py" --no-setup "$@"
    result=$?
    if [ "$result" -ne 75 ]; then exit "$result"; fi
    break
done

tools="$project/.launcher"
uv="$tools/bin/uv"
export UV_UNMANAGED_INSTALL="$tools/bin" UV_NO_MODIFY_PATH=1 UV_NO_CONFIG=1
export UV_PYTHON_INSTALL_DIR="$tools/python" UV_CACHE_DIR="${UV_CACHE_DIR:-$tools/cache}"
if [ ! -x "$uv" ]; then
    mkdir -p "$tools/bin" || exit 1
    mkdir -p "$UV_CACHE_DIR" || exit 1
    installer="$UV_CACHE_DIR/install-uv.sh"
    echo "Downloading the local setup tool..."
    if command -v curl >/dev/null 2>&1; then
        curl -fLsS https://astral.sh/uv/0.12.21/install.sh -o "$installer" || exit 1
    elif command -v wget >/dev/null 2>&1; then
        wget -q https://astral.sh/uv/0.12.21/install.sh -O "$installer" || exit 1
    else
        echo "Install curl or wget, then run launch.sh again." >&2
        exit 1
    fi
    sh "$installer" || exit 1
fi
echo "Preparing local Python 3.12..."
"$uv" python install 3.12 --no-bin --no-registry || exit $?
python=$("$uv" python find --managed-python --no-project 3.12) || exit $?
exec "$python" "$project/launcher.py" --uv "$uv" "$@"
