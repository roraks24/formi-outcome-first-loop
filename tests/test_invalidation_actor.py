import os
import tempfile
import pytest
from unittest.mock import MagicMock

from controller.store import Store
from controller.governance import GovernanceEngine
from controller.experiments import ExperimentManager
from controller.main import Controller


def test_own_apply_must_not_invalidate_supervisor_edit_must():
    """Verifies that:
    1. Controller's own config applies (actor='controller_auto' or 'controller_approved')
       do NOT invalidate running/pending experiments.
    2. Supervisor edits (actor='supervisor' or external) DO invalidate running experiments.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_inv.db")
        store = Store(db_path)
        try:
            mock_api = MagicMock()
            mock_api.get_config.return_value = {
                "version": 1,
                "config": {
                    "allocations": {"default": {"voice": {"V1": 1.0}}},
                    "prompt_version": "P1",
                },
            }
            mock_api.get_state.return_value = {"credits_remaining": 100.0}

            gov = GovernanceEngine(mock_api, store)
            em = ExperimentManager(store, gov)

            # 1. Register an active experiment for nw_renewal
            wid = "nw_renewal"
            workflow_spec = {
                "workflow_id": wid,
                "options": {"voice": ["V1", "V2"]},
                "objective": {"primary": "engagement_rate"},
                "governance": {"approver_hours": [0, 24], "max_requests_per_24h": 5},
                "daily_batch": {"size": 1000},
                "mid_flight": "allowed",
            }

            # Mock submit_proposal returning a proposal ID
            gov.submit_proposal = MagicMock(return_value="prop_001")

            launched = em.design_and_launch(workflow_spec, "voice", sim_minute=100, sim_ts="D0 01:40")
            assert launched is True

            exp = em.get_active_experiment(wid)
            assert exp is not None
            assert exp.status == "PENDING_APPLY"

            # 2. Simulate controller instance receiving config_changed event from its own apply
            ctrl = Controller.__new__(Controller)
            ctrl.store = store
            ctrl.api = mock_api
            ctrl.experiments = em
            ctrl.governance = gov
            ctrl.current_sim_minute = 110
            ctrl.current_sim_ts = "D0 01:50"
            ctrl.supervisor_edited_dims = {}

            # Event: controller's own approved apply
            ev_own_apply = {
                "type": "config_changed",
                "workflow_id": wid,
                "version": 2,
                "actor": "controller_approved",
                "changed_dims": ["voice"],
            }
            ctrl._handle_config_changed(ev_own_apply)

            # Assert: experiment is NOT invalidated; transitioned to RUNNING
            exp = em.get_active_experiment(wid)
            assert exp is not None
            assert exp.status == "RUNNING"
            assert exp.applied_minute == 110

            # Assert: no invalidation decision recorded
            cur = store._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM decisions WHERE decision_type = 'SUPERVISOR_EDIT_INVALIDATION';")
            assert cur.fetchone()[0] == 0

            # 3. Simulate supervisor edit on the workflow
            ev_supervisor = {
                "type": "config_changed",
                "workflow_id": wid,
                "version": 3,
                "actor": "supervisor",
                "changed_dims": ["voice"],
            }
            ctrl._handle_config_changed(ev_supervisor)

            # Assert: experiment IS now invalidated and removed from active map
            exp = em.get_active_experiment(wid)
            assert exp is None

            # Assert: experiment status in SQLite is INVALIDATED
            cur.execute("SELECT status FROM experiments WHERE workflow_id = ?;", (wid,))
            assert cur.fetchone()[0] == "INVALIDATED"

            # Assert: SUPERVISOR_EDIT_INVALIDATION decision was logged
            cur.execute("SELECT COUNT(*) FROM decisions WHERE decision_type = 'SUPERVISOR_EDIT_INVALIDATION';")
            assert cur.fetchone()[0] == 1
            cur.close()
        finally:
            store.close()
