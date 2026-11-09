#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/formi_run"

export PATH="$HOME/formi_env/bin:$PATH"
export VIRTUAL_ENV="$HOME/formi_env"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

PORT=8799
SIM_URL="http://127.0.0.1:$PORT"
RUN_DIR="runs/wsl_run01"
BASE_DIR="runs/wsl_base01"
SCENARIO="scenarios/public_01_dev.yaml"

echo "=== 1. Generating baseline ==="
python -m sim baseline --scenario "$SCENARIO" --run-dir "$BASE_DIR"

echo "=== 2. Starting simulator on port $PORT ==="
python -m sim serve --scenario "$SCENARIO" --run-dir "$RUN_DIR" --port "$PORT" --force &
SIM_PID=$!

cleanup() {
    kill $SIM_PID 2>/dev/null || true
    wait $SIM_PID 2>/dev/null || true
}
trap cleanup EXIT

# Wait for server
for i in {1..30}; do
    if curl -s "$SIM_URL/health" >/dev/null; then
        echo "Simulator ready."
        break
    fi
    sleep 0.5
done

echo "=== 3. Running ./run.sh to completion on public_01 under WSL ext4 ==="
./run.sh "$SIM_URL" "$RUN_DIR/controller.db"

echo "=== 4. Grading run against baseline ==="
python -m sim grade --baseline "$BASE_DIR" --run "$RUN_DIR" --out reports/wsl_public_01.md

echo "=== 5. Done ==="
