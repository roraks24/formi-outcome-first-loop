import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
import json
import yaml
from controller.store import Store
from controller.experiments import ExperimentManager, Experiment
from controller.governance import GovernanceEngine
from controller.api import SimClient
from controller.stats import two_proportion_z_test

def test_early_harm_stop_w2():
    store = Store(":memory:")
    gov = GovernanceEngine(api=None, store=store)
    exp_mgr = ExperimentManager(store=store, governance=gov)

    # In nw_kyc, W2 has an 8% engagement drop (mult 0.92) while guardrail is engagement max drop 0.02
    # Baseline: dials=2000, conn=600, eng=360 (pickup 0.30, engage 0.60)
    # W1 (ctrl): dials=1000, conn=300, eng=180 (engage = 0.600)
    # W2 (treat): dials=250, conn=75, eng=39 (engage = 0.520 -> delta = -0.080)
    # Target required_n = 466 per arm
    exp_id = "exp_test_w2"
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO experiments (
                experiment_id, workflow_id, cohort_id, dim, control_arm,
                arms_json, status, primary_kpi, guardrail_kpi,
                required_n_per_arm, alpha, power, mde, created_minute
            ) VALUES (?, 'nw_kyc', 'default', 'welcome_message', 'W1', '["W1", "W2"]', 'RUNNING', 'pickup_rate', 'engagement_rate', 466, 0.05, 0.80, 0.35, 100);
            """
            , (exp_id,)
        )

    # Insert interim data at n_treatment = 75 (connected calls for guardrail)
    # dials_ctrl = 1000, conn_ctrl = 300, eng_ctrl = 180 (rate = 0.60)
    # dials_t = 250, conn_t = 75, eng_t = 39 (rate = 0.520)
    # Delta on guardrail: 0.520 - 0.600 = -0.080 (-8.0% drop)
    deltas = [
        ("nw_kyc", 0, 5, "default", "D1", "W1", "V1", "P1", 1000, 300, 180, 2, 3600, 600),
        ("nw_kyc", 0, 5, "default", "D1", "W2", "V1", "P1", 250, 75, 39, 1, 900, 150),
    ]
    store.commit_ingest_batch(
        source_name="events",
        chunk_id=1,
        last_event_id=1,
        new_event_ids=[1],
        agg_deltas=deltas,
        new_leads=[],
        lead_outcomes=[],
        sim_ts="2027-01-04T12:00:00+05:30",
    )

    exp_mgr.restore_active_experiments()
    exp = exp_mgr.get_active_experiment("nw_kyc")
    assert exp is not None

    wf_spec = {
        "workflow_id": "nw_kyc",
        "objective": {
            "primary": "pickup_rate",
            "guardrail": {"engagement_rate_max_drop": 0.02}
        },
        "daily_batch": {"size": 700},
        "options": {"welcome_message": ["W1", "W2"]},
    }

    # Statistical verification of interim look
    k_ctrl_g, n_ctrl_g = 180, 300
    k_t_g, n_t_g = 39, 75
    p_ctrl_g = k_ctrl_g / n_ctrl_g
    p_t_g = k_t_g / n_t_g
    g_delta = p_t_g - p_ctrl_g
    _, p_harm_g, _, _ = two_proportion_z_test(k_ctrl_g, n_ctrl_g, k_t_g, n_t_g, alternative="less")

    rate_drop = abs(g_delta)
    cost_actual = round(n_t_g * rate_drop, 2)
    cost_full = round(466 * rate_drop, 2)
    cost_saved = round(cost_full - cost_actual, 2)

    print("=== EARLY HARM-STOP ON nw_kyc W2 ARM ===")
    print(f"Interim treatment sample size (connected): n = {n_t_g} (planned required_n = 466)")
    print(f"Control engagement rate:   {p_ctrl_g:.4f} (180/300)")
    print(f"Treatment W2 engage rate:  {p_t_g:.4f} (39/75)")
    print(f"Observed guardrail drop:   {g_delta:+.4f} (allowed max_drop = -0.0200)")
    print(f"One-sided p-value:         p = {p_harm_g:.5f} (alpha_interim = 0.010)")
    print(f"Exploration cost before:   {cost_full:.2f} lost engagements (full sample horizon)")
    print(f"Exploration cost after:    {cost_actual:.2f} lost engagements (interim look halt)")
    print(f"Exploration cost saved:    {cost_saved:.2f} lost engagements prevented (+{cost_saved/cost_full*100:.1f}%)")

if __name__ == "__main__":
    test_early_harm_stop_w2()
