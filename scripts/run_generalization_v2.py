"""Generalization v2 benchmark script (Item 9).

Runs the controller once with NO TUNING against test_generalization_v2.yaml:
- Different workflow IDs / tenants: apex_collections (apex_finance), horizon_intake (horizon_health)
- Different caller-ID pool: DID_91..94, DID_81..83
- Different policy values: approver_hours [10, 18], max_requests_per_24h 3, min_validation_pass_rate 0.94
- Different seed: 8888
- Incidents on different workflows: DID flagged on apex_collections, bad prompt on horizon_intake, supervisor edit on apex_collections
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from controller.main import Controller


def wait_for_server(url: str, timeout_s: float = 60.0) -> None:
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
    sim_port = 8794
    sim_url = f"http://127.0.0.1:{sim_port}"
    scenario = "scenarios/test_generalization_v2.yaml"
    base_dir = Path("runs/base_gen_v2")
    run_dir = Path("runs/mine_gen_v2")
    report_file = Path("reports/generalization_v2_test.md")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "controller.db"

    print("=" * 80)
    print("GENERALIZATION V2 BENCHMARK (test_generalization_v2)")
    print("Tenants: apex_finance, horizon_health | Seed: 8888")
    print("=" * 80)

    # 1. Generate baseline if missing
    if not base_dir.exists():
        print(f"[GEN2] Generating baseline at {base_dir}...")
        t_base_0 = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "sim", "baseline", "--scenario", scenario, "--run-dir", str(base_dir)],
            check=True,
        )
        print(f"[GEN2] Baseline generated in {time.perf_counter() - t_base_0:.2f}s.")

    # Clean previous run
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

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
    print(f"[GEN2] Launching simulator: {' '.join(server_cmd)}")
    server_proc = subprocess.Popen(server_cmd)

    try:
        wait_for_server(sim_url, timeout_s=30.0)
        print(f"[GEN2] Simulator server ready at {sim_url}")

        # 3. Run controller autonomously
        t_ctrl_0 = time.perf_counter()
        controller = Controller(sim_url=sim_url, db_path=str(db_path), tick_minutes=15)
        controller.run()
        ctrl_duration = time.perf_counter() - t_ctrl_0
        print(f"[GEN2] Controller finished in {ctrl_duration:.2f} s")

        # 4. Grade against baseline
        print("\n" + "=" * 70)
        print("GRADING RUN AGAINST BASELINE")
        print("=" * 70)
        grade_res = subprocess.run(
            [
                sys.executable,
                "-m",
                "sim",
                "grade",
                "--baseline",
                str(base_dir),
                "--run",
                str(run_dir),
                "--out",
                str(report_file),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        grade_output = grade_res.stdout
        print(grade_output)

        with open(report_file, "w", encoding="utf-8") as f:
            f.write(grade_output)
        print(f"[GEN2] Report saved to {report_file}")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            server_proc.kill()


if __name__ == "__main__":
    main()
