#!/usr/bin/env bash
set -euo pipefail

# Crash recovery demonstration script (C13)
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

python scripts/crash_demo.py
