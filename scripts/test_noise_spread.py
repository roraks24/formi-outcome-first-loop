import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.grader import grade


def wait_for_server(url: str, timeout_s: float = 30.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            r = httpx.get(f"{url}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Server at {url} did not start within {timeout_s}s")


def run_noop_instance(run_idx: int, extra_gets_per_tick: int, port: int, scenario: str, base_dir: Path) -> dict:
    run_dir = Path(f"runs/noise_run_{run_idx}")
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    sim_url = f"http://127.0.0.1:{port}"
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
        str(port),
        "--force",
    ]
    proc = subprocess.Popen(server_cmd)
    try:
        wait_for_server(sim_url)

        # No-op loop: advances simulation via /sim/advance, issuing extra GET requests
        client = httpx.Client(base_url=sim_url, timeout=30.0)
        while True:
            advance_res = client.post("/sim/advance", json={"minutes": 15}).json()
            finished = advance_res.get("finished", False)

            for _ in range(extra_gets_per_tick):
                client.get("/sim/state")
                client.get("/workflows")

            if finished:
                break
        client.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except Exception:
            proc.kill()

    # Grade against baseline
    g = grade(str(run_dir), str(base_dir))
    return g


def main():
    scenario = "scenarios/public_01_dev.yaml"
    base_dir = Path("runs/base01")
    if not base_dir.exists():
        subprocess.run([sys.executable, "-m", "sim", "baseline", "--scenario", scenario, "--run-dir", str(base_dir)], check=True)

    configs = [
        (1, 0),     # Run 1: 0 extra GETs
        (2, 5),     # Run 2: 5 extra GETs
        (3, 10),    # Run 3: 10 extra GETs
        (4, 20),    # Run 4: 20 extra GETs
        (5, 50),    # Run 5: 50 extra GETs
    ]

    results = []
    print("=" * 70)
    print("RUNNING 5x NO-OP CONTROLLER WITH VARYING EXTRA GET CALLS")
    print("=" * 70)

    for run_idx, extra_gets in configs:
        port = 8780 + run_idx
        t0 = time.time()
        print(f"[RUN {run_idx}] Starting with {extra_gets} extra GETs/tick on port {port}...")
        g = run_noop_instance(run_idx, extra_gets, port, scenario, base_dir)
        elapsed = time.time() - t0
        print(f"[RUN {run_idx}] Finished in {elapsed:.1f}s")
        results.append((run_idx, extra_gets, g))

    print("\n" + "=" * 70)
    print("NOISE SPREAD ANALYSIS ACROSS 5 RUNS")
    print("=" * 70)

    workflows = ["ks_cancel", "nw_kyc", "nw_renewal", "ALL"]
    metrics = ["pickup_rate", "engagement_rate", "conclusion_rate"]

    for wf in workflows:
        print(f"\n--- Workflow: {wf} ---")
        for m in metrics:
            deltas = []
            raw_vals = []
            for run_idx, extra_gets, g in results:
                kpis = g.get("kpis", {}).get(wf, {})
                d = g.get("delta", {}).get(wf, {}).get(m, 0.0) or 0.0
                deltas.append(d)
                raw_vals.append(kpis.get(m, 0.0))

            mean_d = float(np.mean(deltas))
            std_d = float(np.std(deltas))
            min_d = float(np.min(deltas))
            max_d = float(np.max(deltas))
            spread = max_d - min_d

            print(f"  {m:18s}: raw={[round(v, 4) for v in raw_vals]} | delta spread = [{min_d:+.4%}, {max_d:+.4%}] | mean = {mean_d:+.4%}, std = {std_d:.4%}, spread = {spread:.4%}")


if __name__ == "__main__":
    main()
