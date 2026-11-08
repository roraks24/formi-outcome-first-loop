#!/usr/bin/env bash
# Formi Outcome-First Agent Loop Controller Runner
# Docker: Not used. Runs directly on Python 3.11+ using SQLite WAL persistence.
set -euo pipefail

SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
DB_PATH="${2:-runs/controller.db}"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

exec python -m controller.main --url "$SIM_URL" --db "$DB_PATH" --tick 15
