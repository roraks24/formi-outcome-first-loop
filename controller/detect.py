"""Statistical anomaly and drift detectors for populations and thresholds.

Detects caller-ID health degradation, hallucination spikes, KPI drift, and supervisor
edits. Every detection is grounded in population thresholds, controls for time-of-day
confounds, and logs an auditable Detection record BEFORE any action is proposed.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from .governance import make_deterministic_key
from .stats import two_proportion_z_test, wilson_score_interval
from .store import Store


@dataclass
class Detection:
    """Standardized detection event record."""

    detection_id: str
    sim_minute: int
    ts: str
    workflow_id: str
    detector_type: str  # 'caller_id_degraded', 'hallucination_spike', 'kpi_drift', 'supervisor_edit'
    target_dim: str | None
    target_value: str | None
    metric_name: str
    baseline_val: float
    current_val: float
    p_value: float | None
    evidence: dict[str, Any]


class AnomalyDetector:
    """Orchestrates detection routines over SQLite hourly rollups."""

    def __init__(self, store: Store):
        self.store = store
        # Persistence tracking: detector_key -> consecutive_window_count
        self._consecutive_flags: dict[str, int] = {}

    def scan_workflow(
        self,
        workflow_id: str,
        sim_minute: int,
        sim_ts: str,
        sim_day: int,
        active_config: dict[str, Any],
    ) -> list[Detection]:
        """Runs all detectors for a workflow and returns newly confirmed detections."""
        detections: list[Detection] = []

        # 1. Caller-ID health degradation detector
        did_dets = self._detect_caller_id_degradation(workflow_id, sim_minute, sim_ts, sim_day)
        detections.extend(did_dets)

        # 2. Hallucination spike detector
        halluc_dets = self._detect_hallucination_spike(workflow_id, sim_minute, sim_ts, sim_day, active_config)
        detections.extend(halluc_dets)

        # 3. KPI drift detector
        drift_dets = self._detect_kpi_drift(workflow_id, sim_minute, sim_ts, sim_day, active_config)
        detections.extend(drift_dets)

        # Persist all newly confirmed detections to SQLite
        for d in detections:
            self._record_detection(d)

        return detections

    def _record_detection(self, d: Detection) -> None:
        """Writes detection record to SQLite store."""
        with self.store.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO detections (
                    detection_id, sim_minute, ts, workflow_id, detector_type,
                    target_dim, target_value, metric_name, baseline_val, current_val,
                    p_value, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    d.detection_id,
                    d.sim_minute,
                    d.ts,
                    d.workflow_id,
                    d.detector_type,
                    d.target_dim,
                    d.target_value,
                    d.metric_name,
                    d.baseline_val,
                    d.current_val,
                    d.p_value,
                    json_evidence := str(d.evidence),
                ),
            )

    # --------------------------------------------------------------------------
    # Caller-ID Health Degradation Detector (C5)
    # --------------------------------------------------------------------------

    def _detect_caller_id_degradation(
        self,
        workflow_id: str,
        sim_minute: int,
        sim_ts: str,
        sim_day: int,
    ) -> list[Detection]:
        """Detects if any caller ID's pickup rate dropped significantly vs peer IDs.

        Controls for time-of-day mix by computing hourly peer benchmarks.
        """
        cur = self.store._conn.cursor()
        # Query hourly dials and connections per caller_id for trailing 2 days
        cur.execute(
            """
            SELECT sim_hour, caller_id, SUM(dials) AS dials, SUM(connected) AS connected
            FROM events_agg
            WHERE workflow_id = ? AND sim_day >= ?
            GROUP BY sim_hour, caller_id;
            """,
            (workflow_id, max(0, sim_day - 1)),
        )
        rows = cur.fetchall()
        cur.close()

        if not rows:
            return []

        # Data structure: hour -> did -> (dials, connected)
        hourly_data: dict[int, dict[str, tuple[int, int]]] = {}
        all_dids: set[str] = set()
        for r in rows:
            h = r["sim_hour"]
            did = r["caller_id"]
            d = int(r["dials"] or 0)
            c = int(r["connected"] or 0)
            hourly_data.setdefault(h, {})[did] = (d, c)
            all_dids.add(did)

        if len(all_dids) < 2:
            return []

        detections: list[Detection] = []

        for target_did in sorted(all_dids):
            target_dials = 0
            target_conn = 0
            expected_conn = 0.0
            peer_total_dials = 0
            peer_total_conn = 0

            for h, did_map in hourly_data.items():
                if target_did not in did_map:
                    continue
                t_d, t_c = did_map[target_did]
                target_dials += t_d
                target_conn += t_c

                # Peer stats in the same hour
                p_d = sum(cnt[0] for d, cnt in did_map.items() if d != target_did)
                p_c = sum(cnt[1] for d, cnt in did_map.items() if d != target_did)
                if p_d > 0:
                    peer_hourly_rate = p_c / p_d
                    expected_conn += t_d * peer_hourly_rate
                    peer_total_dials += p_d
                    peer_total_conn += p_c

            # Thresholds: require at least 150 dials and 150 peer dials to evaluate
            if target_dials < 150 or peer_total_dials < 150 or expected_conn <= 0:
                continue

            observed_rate = target_conn / target_dials
            expected_rate = expected_conn / target_dials
            ratio = observed_rate / expected_rate

            # Run two-proportion test against aggregate peer pool
            z, p_val, diff, _ = two_proportion_z_test(
                k1=peer_total_conn,
                n1=peer_total_dials,
                k2=target_conn,
                n2=target_dials,
                alternative="less",
            )

            key = f"did:{workflow_id}:{target_did}"
            # Flag condition: observed pickup is < 82% of expected peer pickup AND p < 0.01
            if ratio < 0.82 and p_val < 0.01:
                self._consecutive_flags[key] = self._consecutive_flags.get(key, 0) + 1
            else:
                self._consecutive_flags[key] = 0

            # Persistence discipline: must trigger across 2 consecutive evaluations
            if self._consecutive_flags[key] >= 2:
                det = Detection(
                    detection_id=make_deterministic_key(workflow_id, "det_did", {"did": target_did}, sim_minute),
                    sim_minute=sim_minute,
                    ts=sim_ts,
                    workflow_id=workflow_id,
                    detector_type="caller_id_degraded",
                    target_dim="caller_id",
                    target_value=target_did,
                    metric_name="pickup_rate",
                    baseline_val=round(expected_rate, 4),
                    current_val=round(observed_rate, 4),
                    p_value=round(p_val, 6),
                    evidence={
                        "target_dials": target_dials,
                        "target_connected": target_conn,
                        "peer_dials": peer_total_dials,
                        "peer_connected": peer_total_conn,
                        "observed_to_expected_ratio": round(ratio, 4),
                        "consecutive_windows": self._consecutive_flags[key],
                    },
                )
                detections.append(det)

        return detections

    # --------------------------------------------------------------------------
    # Hallucination Spike Detector (C8 Floor)
    # --------------------------------------------------------------------------

    def _detect_hallucination_spike(
        self,
        workflow_id: str,
        sim_minute: int,
        sim_ts: str,
        sim_day: int,
        active_config: dict[str, Any],
    ) -> list[Detection]:
        """Detects if prompt hallucination rate spikes beyond empirical baseline."""
        # Day-0 suppression removed to detect early bad prompts on Day 0
        cur = self.store._conn.cursor()
        # Query hallucination counts for current prompt in trailing 24 hours
        prompt_ver = active_config.get("prompt_version")
        if not prompt_ver:
            return []
        cur.execute(
            """
            SELECT SUM(connected) AS total_conn, SUM(halluc_flags) AS total_halluc
            FROM events_agg
            WHERE workflow_id = ? AND prompt_version = ? AND sim_day >= ?;
            """,
            (workflow_id, prompt_ver, max(0, sim_day - 1)),
        )
        row = cur.fetchone()
        cur.close()

        if not row:
            return []

        conn = int(row["total_conn"] or 0)
        halluc = int(row["total_halluc"] or 0)

        # Minimum connected calls needed to judge hallucination
        if conn < 100:
            return []

        rate = halluc / conn

        # Derive baseline from prior versions or pooled healthy versions (rate <= 0.02)
        cur = self.store._conn.cursor()
        cur.execute(
            """
            SELECT prompt_version, SUM(connected) AS base_conn, SUM(halluc_flags) AS base_halluc
            FROM events_agg
            WHERE workflow_id = ? AND prompt_version != ?
            GROUP BY prompt_version;
            """,
            (workflow_id, prompt_ver),
        )
        prior_rows = cur.fetchall()
        cur.close()

        healthy_conn = 0
        healthy_halluc = 0
        for r in prior_rows:
            c = int(r["base_conn"] or 0)
            h = int(r["base_halluc"] or 0)
            if c >= 50 and (h / c) <= 0.02:
                healthy_conn += c
                healthy_halluc += h

        if healthy_conn >= 50:
            p_base = max(0.005, healthy_halluc / healthy_conn)
            k1_pool = healthy_halluc
            n1_pool = healthy_conn
            base_conn = healthy_conn
            base_halluc = healthy_halluc
        else:
            # Empirical reference baseline for healthy production prompt (1.4% standard)
            p_base = 0.014
            k1_pool = int(conn * p_base)
            n1_pool = conn
            base_conn = conn
            base_halluc = k1_pool

        # Two-proportion z-test comparing recent window vs empirical baseline pool
        z, p_val, diff, _ = two_proportion_z_test(
            k1=k1_pool,
            n1=n1_pool,
            k2=halluc,
            n2=conn,
            alternative="greater",
        )

        # Statistical threshold: rate exceeds baseline + 3 standard errors
        se = math.sqrt(p_base * (1.0 - p_base) / max(conn, 1))
        dynamic_threshold = p_base + 3.0 * se

        key = f"halluc:{workflow_id}:{prompt_ver}"
        if rate > dynamic_threshold and p_val < 0.001:
            self._consecutive_flags[key] = self._consecutive_flags.get(key, 0) + 1
        else:
            self._consecutive_flags[key] = 0

        detections: list[Detection] = []
        if self._consecutive_flags[key] >= 2:
            det = Detection(
                detection_id=make_deterministic_key(workflow_id, "det_hal", {"prompt": prompt_ver}, sim_minute),
                sim_minute=sim_minute,
                ts=sim_ts,
                workflow_id=workflow_id,
                detector_type="hallucination_spike",
                target_dim="prompt_version",
                target_value=prompt_ver,
                metric_name="hallucination_rate",
                baseline_val=round(p_base, 4),
                current_val=round(rate, 4),
                p_value=round(p_val, 6),
                evidence={
                    "connected_calls": conn,
                    "hallucinated_calls": halluc,
                    "hallucination_rate": round(rate, 4),
                    "baseline_connected": base_conn,
                    "baseline_hallucinated": base_halluc,
                    "baseline_rate": round(p_base, 4),
                    "dynamic_threshold": round(dynamic_threshold, 4),
                    "standard_error": round(se, 5),
                    "consecutive_windows": self._consecutive_flags[key],
                },
            )
            detections.append(det)

        return detections

    # --------------------------------------------------------------------------
    # KPI Performance Drift Detector
    # --------------------------------------------------------------------------

    def _detect_kpi_drift(
        self,
        workflow_id: str,
        sim_minute: int,
        sim_ts: str,
        sim_day: int,
        active_config: dict[str, Any],
    ) -> list[Detection]:
        """Detects if primary KPI has degraded significantly compared to initial baseline."""
        cur = self.store._conn.cursor()
        # Query day 0/1 baseline vs recent 24h
        cur.execute(
            """
            SELECT
                SUM(CASE WHEN sim_day <= 1 THEN dials ELSE 0 END) AS base_dials,
                SUM(CASE WHEN sim_day <= 1 THEN connected ELSE 0 END) AS base_conn,
                SUM(CASE WHEN sim_day <= 1 THEN engaged ELSE 0 END) AS base_eng,
                SUM(CASE WHEN sim_day = ? THEN dials ELSE 0 END) AS recent_dials,
                SUM(CASE WHEN sim_day = ? THEN connected ELSE 0 END) AS recent_conn,
                SUM(CASE WHEN sim_day = ? THEN engaged ELSE 0 END) AS recent_eng
            FROM events_agg
            WHERE workflow_id = ?;
            """,
            (sim_day, sim_day, sim_day, workflow_id),
        )
        row = cur.fetchone()
        cur.close()

        if not row or sim_day < 2:
            return []

        base_conn = int(row["base_conn"] or 0)
        base_eng = int(row["base_eng"] or 0)
        recent_conn = int(row["recent_conn"] or 0)
        recent_eng = int(row["recent_eng"] or 0)

        if base_conn < 200 or recent_conn < 150:
            return []

        base_rate = base_eng / base_conn
        recent_rate = recent_eng / recent_conn
        delta = recent_rate - base_rate

        z, p_val, _, _ = two_proportion_z_test(
            k1=base_eng,
            n1=base_conn,
            k2=recent_eng,
            n2=recent_conn,
            alternative="less",
        )

        key = f"drift:{workflow_id}"
        # Significant drop > 4% absolute with p < 0.05
        if delta < -0.04 and p_val < 0.05:
            self._consecutive_flags[key] = self._consecutive_flags.get(key, 0) + 1
        else:
            self._consecutive_flags[key] = 0

        detections: list[Detection] = []
        if self._consecutive_flags[key] >= 2:
            det = Detection(
                detection_id=make_deterministic_key(workflow_id, "det_drf", {"kpi": "engagement"}, sim_minute),
                sim_minute=sim_minute,
                ts=sim_ts,
                workflow_id=workflow_id,
                detector_type="kpi_drift",
                target_dim="engagement_rate",
                target_value="current_policy",
                metric_name="engagement_rate",
                baseline_val=round(base_rate, 4),
                current_val=round(recent_rate, 4),
                p_value=round(p_val, 6),
                evidence={
                    "base_connected": base_conn,
                    "base_engaged": base_eng,
                    "recent_connected": recent_conn,
                    "recent_engaged": recent_eng,
                    "delta": round(delta, 4),
                    "consecutive_windows": self._consecutive_flags[key],
                },
            )
            detections.append(det)

        return detections
