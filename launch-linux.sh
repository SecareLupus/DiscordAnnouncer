#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() {
  printf '[discord-webhook-notifier] %s\n' "$*"
}

choose_python() {
  if [[ -n "${PYTHON:-}" ]] && command -v "$PYTHON" >/dev/null 2>&1; then
    echo "$PYTHON"
    return
  fi
  for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
      echo "$cmd"
      return
    fi
  done
  log "Python 3.9+ is required but was not found in PATH."
  exit 1
}

PYTHON_BIN="$(choose_python)"

if ! "$PYTHON_BIN" - <<'PY'; then
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
  log "Python 3.9+ is required."
  exit 1
fi

VENV_DIR="${VENV_DIR:-$ROOT/.venv}"
VENV_PY="$VENV_DIR/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  log "Creating virtual environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# Activate to ensure the correct python/pip are on PATH for any subprocesses.
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
log "Using virtual environment at $VENV_DIR"

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

exec python -m src.notifier.gui_tk "$@"
