import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import sqlite3
from controller.store import Store
from controller.detect import AnomalyDetector
from controller.incidents import IncidentHandler
from controller.governance import GovernanceEngine
from controller.api import SimClient

def test_day_0_bad_prompt_handling():
    store = Store(":memory:")
    # Insert D0 data with bad prompt P2: 25 hallucination flags out of 500 connected calls (5.0%)
    deltas = [
        ("ks_cancel", 0, 10, "default", "D1", "W1", "V1", "P2", 1000, 500, 200, 25, 6000, 1000),
    ]
    store.commit_ingest_batch(
        source_name="events",
        chunk_id=1,
        last_event_id=1,
        new_event_ids=[1],
        agg_deltas=deltas,
        new_leads=[],
        lead_outcomes=[],
        sim_ts="2027-01-04T10:00:00+05:30",
    )

    detector = AnomalyDetector(store)
    cfg = {"prompt_version": "P2"}

    # Window 1 on Day 0
    dets1 = detector.scan_workflow("ks_cancel", sim_minute=600, sim_ts="2027-01-04T10:00:00+05:30", sim_day=0, active_config=cfg)
    assert len(dets1) == 0

    # Window 2 on Day 0 (30 minutes later)
    dets2 = detector.scan_workflow("ks_cancel", sim_minute=630, sim_ts="2027-01-04T10:30:00+05:30", sim_day=0, active_config=cfg)
    assert len(dets2) == 1, "Should flag on Day 0 without suppression!"

    det = dets2[0]
    assert det.target_value == "P2"
    assert det.current_val == 0.05
    assert det.baseline_val == 0.014 # empirical baseline when no healthy prior version
    print(f"Day-0 bad prompt detected successfully: rate={det.current_val:.4f}, p_base={det.baseline_val:.4f}, p={det.p_value}")

if __name__ == "__main__":
    test_day_0_bad_prompt_handling()
    print("ALL TESTS PASSED")
