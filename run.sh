#!/usr/bin/env bash
# Formi Outcome-First Agent Loop Controller Runner
set -euo pipefail

SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
DB_PATH="${2:-data/controller.db}"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

PY_BIN="python3"
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PY_BIN="$VIRTUAL_ENV/bin/python"
elif [ -x "$HOME/formi_env/bin/python" ]; then
    PY_BIN="$HOME/formi_env/bin/python"
elif [ -x "/tmp/formi_venv/bin/python" ]; then
    PY_BIN="/tmp/formi_venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PY_BIN="python"
fi

exec "$PY_BIN" -m controller.main --url "$SIM_URL" --db "$DB_PATH" --tick 15
