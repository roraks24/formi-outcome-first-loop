"""Generalization benchmark test script (Item 8).

Runs the controller against test_02_generalization.yaml (new seed, shifted incident days,
and different workflows hit by incidents) to prove the controller does not hardcode
scenario parameters, workflow names, or incident timings.
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
    sim_port = 8799
    sim_url = f"http://127.0.0.1:{sim_port}"
    scenario = "scenarios/test_02_generalization.yaml"
    base_dir = Path("runs/base_gen02")
    run_dir = Path("runs/mine_gen02")
    report_file = Path("reports/generalization_test.md")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "controller.db"

    print("=" * 80)
    print("GENERALIZATION BENCHMARK (test_02_generalization)")
    print("Seed: 777 | DID flagged on nw_renewal | Bad prompt on nw_renewal | Supervisor edit on ks_cancel")
    print("=" * 80)

    # 1. Generate baseline if missing
    if not base_dir.exists():
        print(f"[GEN] Generating baseline at {base_dir}...")
        t_base_0 = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "sim", "baseline", "--scenario", scenario, "--run-dir", str(base_dir)],
            check=True,
        )
        print(f"[GEN] Baseline generated in {time.perf_counter() - t_base_0:.2f}s.")

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
    print(f"[GEN] Launching simulator: {' '.join(server_cmd)}")
    server_proc = subprocess.Popen(server_cmd)

    try:
        wait_for_server(sim_url, timeout_s=60.0)
        print(f"[GEN] Simulator ready at {sim_url}")

        t0 = time.perf_counter()
        controller = Controller(sim_url=sim_url, db_path=str(db_path), tick_minutes=15)
        controller.run()
        total_time_s = time.perf_counter() - t0
        print(f"[GEN] Controller finished in {total_time_s:.2f}s.")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            server_proc.kill()

    # 3. Grade against baseline
    print("\n[GEN] Grading against baseline...")
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
    res = subprocess.run(grade_cmd, env=env, capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)

    # 4. Detailed Incident Verification Analysis
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Query proposals
    cur.execute("SELECT proposal_id, workflow_id, status, change_summary, rationale, applied_version FROM proposals;")
    props = cur.fetchall()

    # Query detections
    cur.execute("SELECT detection_id, sim_minute, workflow_id, detector_type, target_dim, target_value FROM detections;")
    dets = cur.fetchall()

    # Query decisions
    cur.execute("SELECT decision_id, sim_minute, workflow_id, decision_type, outcome FROM decisions;")
    decs = cur.fetchall()

    conn.close()

    print("\n" + "=" * 80)
    print("INCIDENT VERIFICATION ON GENERALIZED SCENARIO")
    print("=" * 80)
    print(f"Total Detections: {len(dets)}")
    for d in dets:
        print(f"  - [{d[1]}m] wf={d[2]} type={d[3]} dim={d[4]} val={d[5]}")

    print(f"\nTotal Proposals: {len(props)}")
    for p in props:
        print(f"  - id={p[0]} wf={p[1]} status={p[2]} v={p[5]} summary={p[3]}")

    with open(report_file, "a", encoding="utf-8") as f:
        f.write("\n## Generalization Incident Handling Verification\n\n")
        f.write(f"- **Total Detections Logged**: {len(dets)}\n")
        f.write(f"- **Total Proposals Executed**: {len(props)}\n\n")
        f.write("### Incident Triage Summary\n\n")
        for d in dets:
            f.write(f"- Min {d[1]}: Workflow `{d[2]}` flagged by `{d[3]}` on `{d[4]}={d[5]}`\n")
        f.write("\n### Proposals Summary\n\n")
        for p in props:
            f.write(f"- Proposal `{p[0]}` ({p[1]}): Status **{p[2]}** (applied v{p[5]}) — {p[3]}\n")

    print(f"\n[GEN] Report appended to {report_file}")


if __name__ == "__main__":
    main()
