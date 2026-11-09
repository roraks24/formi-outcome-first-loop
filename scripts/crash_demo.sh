#!/usr/bin/env bash
set -euo pipefail

# Crash recovery demonstration script across four kill points (C13):
# 1. Mid-Ingest (cursor checkpointing)
# 2. Pre-Submit (write-ahead intent logging)
# 3. Post-Submit (pending approval reconciliation)
# 4. Post-Apply (applied version config reconciliation)
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

PYTHON_CMD=python3
if ! command -v python3 &>/dev/null; then
    PYTHON_CMD=python
fi
$PYTHON_CMD scripts/crash_demo.py
