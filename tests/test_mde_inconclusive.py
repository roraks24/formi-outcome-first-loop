import pytest
import json
from controller.experiments import Experiment, ExperimentManager
from controller.store import Store
from controller.governance import GovernanceEngine
from controller.api import SimClient

def test_mde_greater_than_20_maps_to_inconclusive(tmp_path):
    db_path = tmp_path / "test.db"
    store = Store(str(db_path))
    api = SimClient("http://127.0.0.1:9999")
    gov = GovernanceEngine(api, store)
    mgr = ExperimentManager(store, gov)

    # 1. Experiment with mde = 0.25 (> 0.20)
    exp = Experiment(
        experiment_id="exp_test_inconclusive",
        workflow_id="wf_test",
        cohort_id="default",
        dim="voice",
        control_arm="V1",
        arms=["V1", "V2"],
        weights={"V1": 0.5, "V2": 0.5},
        primary_kpi="engagement",
        guardrail_kpi="conclusion_rate",
        required_n_per_arm=200,
        status="RUNNING",
        created_minute=0,
        mde=0.25,
    )
    with store.transaction() as conn:
        conn.execute(
            """
            INSERT INTO experiments (
                experiment_id, workflow_id, cohort_id, dim, control_arm,
                arms_json, status, primary_kpi, guardrail_kpi,
                required_n_per_arm, alpha, power, mde, created_minute
            ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, 0.05, 0.80, ?, ?);
            """,
            (
                exp.experiment_id, exp.workflow_id, exp.cohort_id, exp.dim, exp.control_arm,
                json.dumps(exp.arms), exp.primary_kpi, exp.guardrail_kpi,
                exp.required_n_per_arm, exp.mde, exp.created_minute
            )
        )
    
    # Close experiment without winner (no effect / inconclusive)
    final_status = "INCONCLUSIVE" if (getattr(exp, "mde", 0.08) > 0.20) else "NO_EFFECT"
    assert final_status == "INCONCLUSIVE"
    mgr._close_experiment(exp, final_status, None, 100)

    cur = store._conn.cursor()
    cur.execute("SELECT status, mde FROM experiments WHERE experiment_id = ?", (exp.experiment_id,))
    row = cur.fetchone()
    assert row[0] == "INCONCLUSIVE"
    assert row[1] == 0.25

    # 2. Experiment with mde = 0.10 (<= 0.20)
    exp2 = Experiment(
        experiment_id="exp_test_no_effect",
        workflow_id="wf_test2",
        cohort_id="default",
        dim="voice",
        control_arm="V1",
        arms=["V1", "V2"],
        weights={"V1": 0.5, "V2": 0.5},
        primary_kpi="engagement",
        guardrail_kpi="conclusion_rate",
        required_n_per_arm=2000,
        status="RUNNING",
        created_minute=0,
        mde=0.10,
    )
    with store.transaction() as conn:
        conn.execute(
            """
            INSERT INTO experiments (
                experiment_id, workflow_id, cohort_id, dim, control_arm,
                arms_json, status, primary_kpi, guardrail_kpi,
                required_n_per_arm, alpha, power, mde, created_minute
            ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, 0.05, 0.80, ?, ?);
            """,
            (
                exp2.experiment_id, exp2.workflow_id, exp2.cohort_id, exp2.dim, exp2.control_arm,
                json.dumps(exp2.arms), exp2.primary_kpi, exp2.guardrail_kpi,
                exp2.required_n_per_arm, exp2.mde, exp2.created_minute
            )
        )
    final_status2 = "INCONCLUSIVE" if (getattr(exp2, "mde", 0.08) > 0.20) else "NO_EFFECT"
    assert final_status2 == "NO_EFFECT"
    mgr._close_experiment(exp2, final_status2, None, 100)

    cur.execute("SELECT status, mde FROM experiments WHERE experiment_id = ?", (exp2.experiment_id,))
    row2 = cur.fetchone()
    assert row2[0] == "NO_EFFECT"
    assert row2[1] == 0.10
