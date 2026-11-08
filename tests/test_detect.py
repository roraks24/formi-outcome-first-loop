"""Unit tests for anomaly detectors (Caller-ID health, hallucination spikes, cohorts)."""
from __future__ import annotations

from pathlib import Path

import pytest

from controller.cohort import Cohort, CohortRegistry
from controller.detect import AnomalyDetector
from controller.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    db_file = tmp_path / "test_detect.db"
    return Store(db_file)


def test_cohort_matching_and_priority(store: Store):
    """Verifies that leads match appropriate cohorts according to feature logic and priority."""
    c_senior_ta = Cohort(
        cohort_id="senior_ta",
        workflow_id="nw_renewal",
        where={"age_band": ["60+"], "lang": ["ta"]},
        priority=1,
    )
    c_young = Cohort(
        cohort_id="young",
        workflow_id="nw_renewal",
        where={"age_band": ["18-30"]},
        priority=2,
    )

    registry = CohortRegistry("nw_renewal", [c_senior_ta, c_young])

    # Lead 1: 60+ and Tamil -> matches senior_ta
    lead1 = {"age_band": "60+", "lang": "ta", "city_tier": "1", "value_band": "mid"}
    assert registry.assign(lead1) == "senior_ta"

    # Lead 2: 18-30 and Hindi -> matches young
    lead2 = {"age_band": "18-30", "lang": "hi", "city_tier": "2", "value_band": "low"}
    assert registry.assign(lead2) == "young"

    # Lead 3: 46-60 and Tamil -> does not match either, falls back to default
    lead3 = {"age_band": "46-60", "lang": "ta", "city_tier": "3", "value_band": "high"}
    assert registry.assign(lead3) == "default"


def test_cohort_hierarchical_fallback():
    """Falls back to parent cohort when sample size is below required power threshold."""
    c_young = Cohort(cohort_id="young", workflow_id="nw_renewal", where={"age_band": ["18-30"]}, parent_id="default")
    registry = CohortRegistry("nw_renewal", [c_young])

    # With only 40 samples (below required 200), must fallback to default
    eff_id, did_fb = registry.fallback("young", sample_size=40, required_min_n=200)
    assert did_fb
    assert eff_id == "default"

    # With 250 samples, does not fallback
    eff_id_ok, did_fb_ok = registry.fallback("young", sample_size=250, required_min_n=200)
    assert not did_fb_ok
    assert eff_id_ok == "young"


def test_caller_id_health_degradation_detection(store: Store):
    """Detects caller ID whose pickup rate is significantly lower than peer caller IDs."""
    detector = AnomalyDetector(store)

    # Insert data for day 0 and day 1:
    # D1 has 10% pickup (20 connected / 200 dials)
    # D2 has 30% pickup (60 connected / 200 dials)
    # D3 has 30% pickup (60 connected / 200 dials)
    deltas = [
        # (workflow_id, sim_day, sim_hour, cohort_id, caller_id, welcome, voice, prompt, dials, conn, eng, hal, dur, turns)
        ("nw_renewal", 0, 10, "default", "D1", "W1", "V1", "P1", 200, 20, 10, 0, 300, 40),
        ("nw_renewal", 0, 10, "default", "D2", "W1", "V1", "P1", 200, 60, 30, 0, 900, 120),
        ("nw_renewal", 0, 10, "default", "D3", "W1", "V1", "P1", 200, 60, 30, 0, 900, 120),
        ("nw_renewal", 1, 10, "default", "D1", "W1", "V1", "P1", 200, 20, 10, 0, 300, 40),
        ("nw_renewal", 1, 10, "default", "D2", "W1", "V1", "P1", 200, 60, 30, 0, 900, 120),
        ("nw_renewal", 1, 10, "default", "D3", "W1", "V1", "P1", 200, 60, 30, 0, 900, 120),
    ]

    store.commit_ingest_batch(
        source_name="events",
        chunk_id=1,
        last_event_id=6,
        new_event_ids=[1, 2, 3, 4, 5, 6],
        agg_deltas=deltas,
        new_leads=[],
        lead_outcomes=[],
        sim_ts="2027-01-05T10:00:00+05:30",
    )

    cfg = {"prompt_version": "P1"}

    # Window 1: Detector observes condition (count = 1)
    dets_w1 = detector.scan_workflow("nw_renewal", sim_minute=1440, sim_ts="2027-01-05T10:00:00+05:30", sim_day=1, active_config=cfg)
    assert len(dets_w1) == 0  # Persistence requirement = 2 windows

    # Window 2: Condition persists (count = 2) -> Emits detection!
    dets_w2 = detector.scan_workflow("nw_renewal", sim_minute=1500, sim_ts="2027-01-05T11:00:00+05:30", sim_day=1, active_config=cfg)
    assert len(dets_w2) == 1
    d = dets_w2[0]
    assert d.detector_type == "caller_id_degraded"
    assert d.target_value == "D1"
    assert d.current_val < d.baseline_val
    assert d.p_value is not None and d.p_value < 0.001


def test_hallucination_spike_detection(store: Store):
    """Detects prompt version whose hallucination rate spikes above the 1% threshold."""
    detector = AnomalyDetector(store)

    # Insert data for a supervisor prompt P2: 25 hallucination flags out of 500 connected calls (5.0%)
    deltas = [
        ("ks_cancel", 2, 12, "default", "D1", "W1", "V1", "P2", 1000, 500, 200, 25, 6000, 1000),
    ]

    store.commit_ingest_batch(
        source_name="events",
        chunk_id=1,
        last_event_id=1,
        new_event_ids=[1],
        agg_deltas=deltas,
        new_leads=[],
        lead_outcomes=[],
        sim_ts="2027-01-06T12:00:00+05:30",
    )

    cfg = {"prompt_version": "P2"}

    # Window 1
    dets_w1 = detector.scan_workflow("ks_cancel", sim_minute=2880, sim_ts="2027-01-06T12:00:00+05:30", sim_day=2, active_config=cfg)
    assert len(dets_w1) == 0

    # Window 2
    dets_w2 = detector.scan_workflow("ks_cancel", sim_minute=2940, sim_ts="2027-01-06T13:00:00+05:30", sim_day=2, active_config=cfg)
    assert len(dets_w2) == 1
    d = dets_w2[0]
    assert d.detector_type == "hallucination_spike"
    assert d.target_value == "P2"
    assert d.current_val == pytest.approx(0.05, abs=1e-4)
    assert d.p_value is not None and d.p_value < 0.001
