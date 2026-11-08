"""Runs the complete controller against public_01_dev and grades results."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure root directory is on PYTHONPATH
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from controller.main import Controller


def wait_for_server(url: str, timeout_s: float = 30.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            r = httpx.get(f"{url}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"Simulator server at {url} did not start within {timeout_s}s")


def main():
    sim_port = 8790
    sim_url = f"http://127.0.0.1:{sim_port}"
    run_dir = Path("runs/mine01")
    base_dir = Path("runs/base01")
    db_path = run_dir / "controller.db"
    scenario = "scenarios/public_01_dev.yaml"
    report_file = Path("reports/public_01.md")
    report_file.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("STEP 2: RUNNING CONTROLLER ON public_01_dev")
    print("=" * 70)

    # 1. Generate baseline if not already present
    if not base_dir.exists():
        print(f"[RUN] Generating baseline at {base_dir}...")
        subprocess.run([sys.executable, "-m", "sim", "baseline", "--scenario", scenario, "--run-dir", str(base_dir)], check=True)

    # 2. Start simulator server
    server_cmd = [
        sys.executable,
        "-m",
        "sim",
        "serve",
        "--scenario",
        scenario,
        "--run-dir",
        str(run_dir),
        "--port",
        str(sim_port),
        "--force",
    ]
    print(f"[RUN] Starting simulator: {' '.join(server_cmd)}")
    server_proc = subprocess.Popen(server_cmd)

    try:
        wait_for_server(sim_url, timeout_s=30.0)
        print(f"[RUN] Simulator server ready at {sim_url}")

        # 3. Run controller
        t0 = time.perf_counter()
        controller = Controller(sim_url=sim_url, db_path=str(db_path), tick_minutes=15)
        controller.run()
        total_time_s = time.perf_counter() - t0
        print(f"[RUN] Controller finished in {total_time_s:.2f} s")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            server_proc.kill()

    # 4. Grade against baseline
    print("\n" + "=" * 70)
    print("GRADING RUN AGAINST BASELINE")
    print("=" * 70)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    grade_cmd = [
        sys.executable,
        "-m",
        "sim",
        "grade",
        "--run",
        str(run_dir),
        "--baseline",
        str(base_dir),
        "--out",
        str(report_file),
    ]
    res = subprocess.run(grade_cmd, capture_output=True, text=True, env=env)
    print(res.stdout)
    if res.stderr:
        print("[STDERR]", res.stderr)


if __name__ == "__main__":
    main()
