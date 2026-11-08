"""Controller main entry point and runtime loop.

Orchestrates clock advancement, streaming ingestion, startup crash recovery,
and structured per-tick logging.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .api import SimApiError, SimClient, SimulatorFinishedError
from .ingest import IngestPipeline
from .store import Store


def get_process_rss_mb() -> float:
    """Returns the process RSS in megabytes on Linux or Windows without extra dependencies."""
    try:
        import resource
        # On Linux, ru_maxrss is in kilobytes
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except ImportError:
        pass

    try:
        import ctypes
        import ctypes.wintypes as wt

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD),
                ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        fn = ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wt.DWORD]
        fn.restype = wt.BOOL

        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:
        pass

    return 0.0


class Controller:
    """Main loop orchestrator managing ingestion, recovery, and advancement."""

    def __init__(
        self,
        sim_url: str = "http://127.0.0.1:8700",
        db_path: str = "runs/controller.db",
        tick_minutes: int = 15,
    ):
        self.sim_url = sim_url
        self.tick_minutes = tick_minutes
        self.store = Store(db_path)
        self.api = SimClient(base_url=sim_url, store=self.store)
        self.ingest = IngestPipeline(
            api=self.api,
            store=self.store,
            on_config_changed=self._handle_config_changed,
            on_approval_decided=self._handle_approval_decided,
        )
        self.running = True
        self.total_events = 0
        self.open_proposals_count = 0
        self._setup_signals()

    def _setup_signals(self) -> None:
        def _stop(signum, frame):
            print(f"\n[CONTROLLER] Received signal {signum}. Initiating graceful shutdown...", file=sys.stderr)
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

    def _handle_config_changed(self, ev: dict[str, Any]) -> None:
        wid = ev.get("workflow_id", "")
        ver = int(ev.get("version", 1))
        actor = ev.get("actor", "")
        minute = 0  # Will be reconciled from sim state if needed
        # Save snapshot
        cfg = self.api.get_config(wid).get("config", {})
        self.store.save_config_snapshot(wid, ver, actor, minute, cfg)

    def _handle_approval_decided(self, ev: dict[str, Any]) -> None:
        aid = ev.get("approval_id", "")
        wid = ev.get("workflow_id", "")
        status = ev.get("status", "")
        reasons = ev.get("reasons", [])
        with self.store.transaction() as cur:
            cur.execute(
                """
                UPDATE proposals
                SET status = ?, rejection_reasons_json = ?
                WHERE approval_id = ?;
                """,
                (status, json.dumps(reasons), aid),
            )

    def recover(self) -> None:
        """Runs startup reconciliation before starting the control loop."""
        print("[RECOVER] Reconciling state with simulator...", file=sys.stderr)
        state = self.api.get_state()
        wfs = self.api.get_workflows()

        # 1. Snapshot latest configurations for all workflows
        for w in wfs:
            wid = w["workflow_id"]
            cfg_resp = self.api.get_config(wid)
            self.store.save_config_snapshot(
                workflow_id=wid,
                version=cfg_resp["version"],
                actor=cfg_resp.get("actor", "business_default"),
                applied_at_minute=None,
                config=cfg_resp["config"],
            )

        # 2. Reconcile pending write-ahead intents
        pending = self.store.get_pending_intents()
        if pending:
            print(f"[RECOVER] Reconciling {len(pending)} pending intents...", file=sys.stderr)
            for row in pending:
                action = row["action"]
                wid = row["workflow_id"]
                key = row["idempotency_key"]
                if action == "request_approval":
                    approvals = self.api.list_approvals(workflow_id=wid)
                    match = next((a for a in approvals if a.get("proposal_id") == key), None)
                    if match:
                        self.store.mark_intent_executed(key, state["ts"], match)
                    else:
                        self.store.mark_intent_failed(key, state["ts"], "not_found_on_sim")
                elif action == "apply_approval":
                    cfg = self.api.get_config(wid)
                    # Check if applied
                    self.store.mark_intent_executed(key, state["ts"], cfg)

        # 3. Synchronize open proposals count
        approvals = self.api.list_approvals(status="pending")
        self.open_proposals_count = len(approvals)

        # 4. Ingest any pending chunks already produced by simulator
        ingest_stats = self.ingest.ingest_available_chunks(kind="events", sim_ts=state["ts"])
        self.total_events += ingest_stats["events"]
        print(
            f"[RECOVER] Completed: Scenario {state['scenario']} at {state['ts']} "
            f"(sim_minute {state['sim_minute']}). Ingested {ingest_stats['events']} prior events.",
            file=sys.stderr,
        )

    def run(self) -> None:
        """Main execution loop: advances simulation, ingests chunks, logs telemetry."""
        self.recover()

        while self.running:
            t0 = time.perf_counter()
            try:
                # Advance simulation by tick_minutes
                adv = self.api.advance(minutes=self.tick_minutes)
            except SimulatorFinishedError:
                print("\n[LOOP] Simulation completed cleanly (409 finished).", file=sys.stderr)
                break
            except SimApiError as e:
                print(f"[ERROR] API advance failed: {e}", file=sys.stderr)
                break

            sim_ts = adv["ts"]
            sim_min = adv["sim_minute"]
            finished = adv.get("finished", False)

            # Ingest new event chunks produced by the advance step
            ingest_stats = self.ingest.ingest_available_chunks(kind="events", sim_ts=sim_ts)
            dt_s = max(time.perf_counter() - t0, 1e-6)
            tick_events = ingest_stats["events"]
            self.total_events += tick_events
            events_per_sec = tick_events / dt_s

            state = self.api.get_state()
            credits_rem = state.get("credits_remaining", 0.0)
            rss_mb = get_process_rss_mb()

            # Structured per-tick telemetry log line
            print(
                f"[TICK] ts={sim_ts} min={sim_min:5d} lag_min=0 "
                f"events_total={self.total_events:7d} events_sec={events_per_sec:7.1f} "
                f"open_props={self.open_proposals_count} credits_rem={credits_rem:6.1f} rss_mb={rss_mb:5.1f}",
                flush=True,
            )

            if finished:
                print("\n[LOOP] Reached end of scenario.", file=sys.stderr)
                break

        print("[CONTROLLER] Finalizing storage...", file=sys.stderr)
        self.store.close()
        self.api.close()
        print("[CONTROLLER] Finished.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Formi Outcome-First Agent Loop Controller")
    parser.add_argument("--url", default="http://127.0.0.1:8700", help="Simulator server URL")
    parser.add_argument("--db", default="runs/controller.db", help="Path to SQLite WAL database")
    parser.add_argument("--tick", type=int, default=15, help="Sim minutes to advance per tick")
    args = parser.parse_args()

    controller = Controller(sim_url=args.url, db_path=args.db, tick_minutes=args.tick)
    controller.run()


if __name__ == "__main__":
    main()
