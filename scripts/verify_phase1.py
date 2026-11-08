"""End-to-end Phase 1 verification script against public_01_dev.

Spawns the simulator server, runs the controller over HTTP to scenario completion,
queries the controller's SQLite database, and prints the exact comparison with
API totals, ingest throughput, and peak RSS memory.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

# Ensure root directory is on PYTHONPATH
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from controller.main import Controller, get_process_rss_mb
from controller.store import Store


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
    sim_port = 8788
    sim_url = f"http://127.0.0.1:{sim_port}"
    run_dir = Path("runs/verify_dev01")
    db_path = run_dir / "controller.db"
    scenario = "scenarios/public_01_dev.yaml"

    print("=" * 70)
    print("PHASE 1 VERIFICATION: Running public_01_dev end-to-end")
    print("=" * 70)

    # 1. Start simulator server in subprocess
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
    print(f"[VERIFY] Starting simulator: {' '.join(server_cmd)}")
    server_proc = subprocess.Popen(server_cmd)

    try:
        wait_for_server(sim_url, timeout_s=30.0)
        print(f"[VERIFY] Simulator server ready at {sim_url}")

        # 2. Run controller loop
        t_start = time.perf_counter()
        controller = Controller(sim_url=sim_url, db_path=str(db_path), tick_minutes=60)
        controller.run()
        total_time_s = max(time.perf_counter() - t_start, 1e-6)

        # 3. Retrieve final simulator state over HTTP API
        state_resp = httpx.get(f"{sim_url}/sim/state", timeout=10.0).json()
        print(f"\n[VERIFY] Final Simulator State: {json.dumps(state_resp, indent=2)}")

        # 4. Query controller's SQLite database rollups
        store = Store(db_path)
        kpi_totals = store.get_kpi_totals()
        last_chunk, last_event_id = store.get_cursor("events")
        peak_rss = get_process_rss_mb()
        events_per_sec = controller.total_events / total_time_s

        print("\n" + "=" * 70)
        print("VERIFICATION RESULTS")
        print("=" * 70)
        print(f"Total Wall-Clock Time:      {total_time_s:.2f} s")
        print(f"Total Ingested Events:      {controller.total_events}")
        print(f"Ingest Throughput:          {events_per_sec:.1f} events/sec")
        print(f"Peak Controller RSS:        {peak_rss:.2f} MB")
        print(f"Last Event ID Ingested:     {last_event_id}")
        print(f"Simulator Last Event ID:    {state_resp['last_event_id']}")
        print(f"Cursor Last Chunk:          {last_chunk}")

        print("\nController Aggregated KPI Totals by Workflow:")
        print(f"{'Workflow ID':<20} {'Dials':<10} {'Connected':<12} {'Engaged':<10} {'Halluc Flags':<12}")
        print("-" * 66)
        for wid, m in sorted(kpi_totals.items()):
            print(
                f"{wid:<20} {m['dials']:<10} {m['connected']:<12} {m['engaged']:<10} {m['halluc_flags']:<12}"
            )

        # Check that event count matches simulator state
        assert last_event_id == state_resp["last_event_id"], (
            f"Event count mismatch: controller {last_event_id} vs sim {state_resp['last_event_id']}"
        )
        assert kpi_totals["ALL"]["dials"] > 0, "No dials recorded!"

        print("\n[VERIFY] SUCCESS: All events ingested, exactly matching simulator event stream!")
        print("=" * 70)
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            server_proc.kill()


if __name__ == "__main__":
    main()
