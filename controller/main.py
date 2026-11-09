"""Outcome-First Agent Loop Controller main orchestration loop.

Coordinates clock advancement, streaming ingestion, anomaly detection,
governance approvals, and statistical experimentation.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .api import SimApiError, SimClient, SimulatorFinishedError
from .detect import AnomalyDetector
from .experiments import ExperimentManager
from .governance import GovernanceEngine
from .incidents import IncidentHandler
from .ingest import IngestPipeline
from .store import Store


def get_process_rss_mb() -> float:
    """Returns process RSS in MB on Linux or Windows without extra dependencies."""
    try:
        import resource
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
    """Always-on controller driving the simulator over HTTP."""

    def __init__(
        self,
        sim_url: str = "http://127.0.0.1:8700",
        db_path: str = "data/controller.db",
        tick_minutes: int = 15,
    ):
        self.sim_url = sim_url
        self.tick_minutes = tick_minutes
        self.store = Store(db_path)
        self.api = SimClient(base_url=sim_url, store=self.store)
        self.governance = GovernanceEngine(api=self.api, store=self.store)
        self.detector = AnomalyDetector(store=self.store)
        self.experiments = ExperimentManager(store=self.store, governance=self.governance)
        self.incidents = IncidentHandler(governance=self.governance, store=self.store)

        self.ingest = IngestPipeline(
            api=self.api,
            store=self.store,
            on_config_changed=self._handle_config_changed,
            on_approval_decided=self._handle_approval_decided,
        )

        self.running = True
        self.total_events = 0
        self.tick_durations: list[float] = []
        self.workflow_specs: dict[str, dict[str, Any]] = {}
        self.supervisor_edited_dims: dict[str, set[str]] = {}
        self.current_sim_minute = 0
        self.current_sim_ts = ""
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
        minute = self.current_sim_minute
        ts = self.current_sim_ts
        cfg = self.api.get_config(wid).get("config", {})
        self.store.save_config_snapshot(wid, ver, actor, minute, cfg)

        # Invalidate active experiments only if external non-controller actor altered configuration
        if not actor.startswith("controller_"):
            changed_dims = ev.get("changed_dims", [])
            self.supervisor_edited_dims.setdefault(wid, set()).update(changed_dims)
            print(f"[SUPERVISOR] Detected external/supervisor edit on {wid} (v{ver}, actor={actor}, dims={changed_dims}). Invalidating experiments.", file=sys.stderr)
            self.experiments.invalidate_for_workflow(wid, f"external_edit_{actor}", minute, ts)
        else:
            # Controller's own apply: notify experiment manager of apply confirmation
            self.experiments.notify_config_applied(wid, ver, minute)

    def _handle_approval_decided(self, ev: dict[str, Any]) -> None:
        aid = ev.get("approval_id", "")
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
        """Startup crash reconciliation before starting control loop."""
        print("[RECOVER] Reconciling state with simulator...", file=sys.stderr)
        state = self.api.get_state()
        wfs = self.api.get_workflows()
        self.workflow_specs = {w["workflow_id"]: w for w in wfs}

        # 1. Snapshot latest configurations
        for wid, w in self.workflow_specs.items():
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
                        aid = match.get("approval_id")
                        payload = json.loads(row["payload_json"])
                        diff = payload.get("diff", {})
                        affected = payload.get("affected_leads", 0)
                        dry_run = payload.get("dry_run", {})
                        impact = payload.get("impact_score", 0.0)
                        rationale = payload.get("rationale", "")
                        summary = payload.get("change_summary", "")
                        base_ver = payload.get("base_version", 1)
                        ttl_hours = payload.get("expires_in_hours", 24)
                        evidence = payload.get("evidence")
                        with self.store.transaction() as cur:
                            cur.execute(
                                """
                                INSERT OR IGNORE INTO proposals (
                                    proposal_id, approval_id, workflow_id, base_version,
                                    status, change_summary, diff_json, affected_leads,
                                    dry_run_json, impact_score, rationale, evidence_json,
                                    is_auto_apply, created_minute, decided_minute, expires_minute
                                ) VALUES (?, ?, ?, ?, 'SUBMITTED', ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?);
                                """,
                                (
                                    key, aid, wid, base_ver, summary,
                                    json.dumps(diff), affected, json.dumps(dry_run),
                                    impact, rationale, json.dumps(evidence) if evidence else None,
                                    state["sim_minute"], state["sim_minute"] + int(ttl_hours * 60),
                                ),
                            )
                        if dry_run and "dimension" in dry_run:
                            dim = dry_run["dimension"]
                            arms = dry_run.get("arms", [])
                            req_n = dry_run.get("required_n", 100)
                            exp_id = dry_run.get("experiment_id") or make_deterministic_key(wid, f"exp_{dim}", diff, base_ver)
                            w_spec = self.workflow_specs.get(wid, {})
                            p_kpi = dry_run.get("primary_kpi") or (w_spec.get("objective", {}).get("primary") if isinstance(w_spec.get("objective", {}).get("primary"), str) else "conclusion_rate")
                            g_kpi = dry_run.get("guardrail_kpi") or ("pickup_rate" if p_kpi == "engagement_rate" else "engagement_rate")
                            ctrl_arm = arms[0] if arms else "control"
                            with self.store.transaction() as cur:
                                cur.execute(
                                    """
                                    INSERT OR IGNORE INTO experiments (
                                        experiment_id, workflow_id, cohort_id, dim, control_arm,
                                        arms_json, status, primary_kpi, guardrail_kpi,
                                        required_n_per_arm, alpha, power, mde, created_minute, applied_minute
                                    ) VALUES (?, ?, 'default', ?, ?, ?, 'PENDING_APPLY', ?, ?, ?, 0.05, 0.80, 0.08, ?, NULL);
                                    """,
                                    (
                                        exp_id, wid, dim, ctrl_arm,
                                        json.dumps(arms), p_kpi, g_kpi,
                                        req_n, state["sim_minute"],
                                    ),
                                )
                    else:
                        self.store.mark_intent_failed(key, state["ts"], "not_found_on_sim")
                elif action == "apply_approval":
                    cfg = self.api.get_config(wid)
                    self.store.mark_intent_executed(key, state["ts"], cfg)
                    payload = json.loads(row["payload_json"])
                    aid = payload.get("approval_id")
                    new_ver = cfg.get("version")
                    with self.store.transaction() as cur:
                        cur.execute(
                            """
                            UPDATE proposals
                            SET status = 'APPLIED', applied_minute = ?, applied_version = ?
                            WHERE approval_id = ?;
                            """,
                            (state["sim_minute"], new_ver, aid),
                        )

        # 3. Restore active running experiments from store
        self.experiments.restore_active_experiments()

        # 4. Ingest prior event chunks
        ingest_stats = self.ingest.ingest_available_chunks(kind="events", sim_ts=state["ts"])
        self.total_events += ingest_stats["events"]
        print(
            f"[RECOVER] Completed: Scenario {state['scenario']} at {state['ts']} "
            f"(sim_minute {state['sim_minute']}). Ingested {ingest_stats['events']} prior events.",
            file=sys.stderr,
        )

    def run(self) -> None:
        """Main execution loop advancing simulation, ingesting, and governing."""
        self.recover()

        while self.running:
            t0 = time.perf_counter()
            try:
                adv = self.api.advance(minutes=self.tick_minutes)
            except SimulatorFinishedError:
                print("\n[LOOP] Simulation completed cleanly (409 finished).", file=sys.stderr)
                break
            except SimApiError as e:
                print(f"[ERROR] API advance failed: {e}", file=sys.stderr)
                break

            sim_ts = adv["ts"]
            sim_min = adv["sim_minute"]
            self.current_sim_minute = sim_min
            self.current_sim_ts = sim_ts
            sim_day = sim_min // 1440
            current_hour = (sim_min % 1440) // 60
            finished = adv.get("finished", False)

            # 1. Ingestion
            ingest_stats = self.ingest.ingest_available_chunks(kind="events", sim_ts=sim_ts)
            dt_s = max(time.perf_counter() - t0, 1e-6)
            tick_events = ingest_stats["events"]
            self.total_events += tick_events
            events_per_sec = tick_events / dt_s

            # 2. Reconcile approved proposals and apply them
            applied_cnt = self.governance.reconcile_and_apply_approved(sim_minute=sim_min, sim_ts=sim_ts)
            if applied_cnt > 0:
                self.experiments.restore_active_experiments()

            # 3. Anomaly detection & Incident Response
            for wid, spec in self.workflow_specs.items():
                cur_cfg = self.api.get_config(wid)["config"]
                detections = self.detector.scan_workflow(
                    workflow_id=wid,
                    sim_minute=sim_min,
                    sim_ts=sim_ts,
                    sim_day=sim_day,
                    active_config=cur_cfg,
                )
                if detections:
                    self.incidents.handle_detections(
                        detections=detections,
                        workflow_specs=self.workflow_specs,
                        sim_minute=sim_min,
                        sim_ts=sim_ts,
                    )

            # 4. Statistical Experimentation & Learning Loop
            # Evaluate after initial baseline period (Day 1+)
            if sim_day >= 1:
                for wid, spec in self.workflow_specs.items():
                    # Check for decay and rollback on previously promoted variants
                    self.experiments.check_decay_and_rollback(spec, sim_min, sim_ts)

                    active_exp = self.experiments.get_active_experiment(wid)
                    if active_exp:
                        # Evaluate active experiment
                        self.experiments.evaluate_and_promote(
                            workflow_spec=spec,
                            sim_minute=sim_min,
                            sim_ts=sim_ts,
                        )
                    else:
                        # Launch new experiment (test voice first, then welcome message)
                        # Propose within approver working hours, respecting supervisor edits
                        if self.governance.is_within_approver_hours(spec, current_hour):
                            sup_dims = self.supervisor_edited_dims.get(wid, set())
                            first_dim = "welcome_message" if "voice" in sup_dims else "voice"
                            second_dim = "voice" if first_dim == "welcome_message" else "welcome_message"

                            launched = False
                            if first_dim not in sup_dims:
                                launched = self.experiments.design_and_launch(
                                    workflow_spec=spec,
                                    dim=first_dim,
                                    sim_minute=sim_min,
                                    sim_ts=sim_ts,
                                )
                            if not launched and second_dim not in sup_dims:
                                self.experiments.design_and_launch(
                                    workflow_spec=spec,
                                    dim=second_dim,
                                    sim_minute=sim_min,
                                    sim_ts=sim_ts,
                                )

            # Open proposals count
            approvals = self.api.list_approvals(status="pending")
            open_props = len(approvals)

            state = self.api.get_state()
            credits_rem = state.get("credits_remaining", 0.0)
            rss_mb = get_process_rss_mb()

            # Structured per-tick telemetry log line
            print(
                f"[TICK] ts={sim_ts} min={sim_min:5d} lag_min=0 "
                f"events_total={self.total_events:7d} events_sec={events_per_sec:7.1f} "
                f"open_props={open_props} credits_rem={credits_rem:6.1f} rss_mb={rss_mb:5.1f}",
                flush=True,
            )
            self.tick_durations.append(time.perf_counter() - t0)

            if finished:
                print("\n[LOOP] Reached end of scenario.", file=sys.stderr)
                break

        print("[CONTROLLER] Finalizing storage...", file=sys.stderr)
        self.store.close()
        self.api.close()
        print("[CONTROLLER] Finished.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Formi Outcome-First Agent Loop Controller")
    parser.add_argument("--url", "--sim-url", dest="url", default="http://127.0.0.1:8700", help="Simulator server URL")
    parser.add_argument("--db", default="data/controller.db", help="Path to SQLite WAL database")
    parser.add_argument("--tick", "--ticks", dest="tick", type=int, default=15, help="Sim minutes to advance per tick")
    args = parser.parse_args()

    controller = Controller(sim_url=args.url, db_path=args.db, tick_minutes=args.tick)
    controller.run()


if __name__ == "__main__":
    main()
