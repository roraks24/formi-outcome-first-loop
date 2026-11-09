import pytest
import sqlite3
import tempfile
import os
from unittest.mock import MagicMock

from controller.store import Store
from controller.governance import GovernanceEngine


def test_crash_between_rejection_and_resubmission_persisted_attempt():
    """Tests that retry/idempotency keys use a persisted attempt counter without sim_minute,
    and that crashing between rejection and resubmission at a different minute asserts exactly one submission.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_retry.db")

        submitted_proposals = []

        def mock_request_approval(body):
            submitted_proposals.append(body["proposal_id"])
            return {"approval_id": f"appr_{len(submitted_proposals)}", "status": "PENDING"}

        mock_api = MagicMock()
        mock_api.request_approval.side_effect = mock_request_approval
        mock_api.get_state.return_value = {"credits_remaining": 100.0}
        mock_api.get_config.return_value = {"version": 1, "config": {"caller_id": "DID_001"}}

        # Initialize Store & Governance at sim_minute 1000
        store = Store(db_path)
        gov = GovernanceEngine(api=mock_api, store=store)

        workflow_spec = {
            "workflow_id": "ks_cancel",
            "current": {"config": {"caller_id": "DID_001"}, "version": 1},
            "governance": {
                "approver_hours": [0, 24],
                "max_requests_per_24h": 5,
                "proposal_ttl_hours": 24,
            },
        }
        proposed_cfg = {"caller_id": "DID_002"}

        try:
            # 1. First submission (Attempt 1) at minute 1000
            p1 = gov.submit_proposal(
                workflow_spec=workflow_spec,
                base_version=1,
                proposed_config=proposed_cfg,
                change_summary="Rotate caller-ID to DID_002",
                rationale="High spam flag rate",
                affected_leads=1000,
                impact_score=0.90,
                dry_run={"action": "rotate_caller_id"},
                sim_minute=1000,
                sim_ts="2027-01-02T10:00:00+05:30",
                is_incident=True,
            )

            assert p1 is not None
            assert p1.endswith("_att1")
            assert len(submitted_proposals) == 1
            assert submitted_proposals[0] == p1

            # Simulate Approver rejects Attempt 1
            cur = store._conn.cursor()
            cur.execute("UPDATE proposals SET status = 'rejected' WHERE proposal_id = ?;", (p1,))
            store._conn.commit()
            cur.close()

            # 2. CRASH! Process dies.
            store.close()

            # 3. RESTART at a DIFFERENT minute (minute 2500)
            store2 = Store(db_path)
            gov2 = GovernanceEngine(api=mock_api, store=store2)

            # Resubmit incident proposal at minute 2500
            p2 = gov2.submit_proposal(
                workflow_spec=workflow_spec,
                base_version=1,
                proposed_config=proposed_cfg,
                change_summary="Rotate caller-ID to DID_002",
                rationale="High spam flag rate (attempt 2)",
                affected_leads=1000,
                impact_score=0.90,
                dry_run={"action": "rotate_caller_id"},
                sim_minute=2500,
                sim_ts="2027-01-03T11:00:00+05:30",
                is_incident=True,
            )

            assert p2 is not None
            assert p2.endswith("_att2")
            # Assert Attempt 2 was submitted
            assert len(submitted_proposals) == 2
            assert submitted_proposals[1] == p2

            # 4. CRASH AGAIN and restart at yet another minute (minute 3200)
            store2.close()
            store3 = Store(db_path)
            gov3 = GovernanceEngine(api=mock_api, store=store3)

            # Attempt to submit again at minute 3200 while Attempt 2 is still pending
            p3 = gov3.submit_proposal(
                workflow_spec=workflow_spec,
                base_version=1,
                proposed_config=proposed_cfg,
                change_summary="Rotate caller-ID to DID_002",
                rationale="High spam flag rate (duplicate call)",
                affected_leads=1000,
                impact_score=0.90,
                dry_run={"action": "rotate_caller_id"},
                sim_minute=3200,
                sim_ts="2027-01-03T22:40:00+05:30",
                is_incident=True,
            )

            assert p3 == p2
            # Assert NO duplicate submission occurred: still exactly 2 total submissions (att1 and att2)
            assert len(submitted_proposals) == 2
            store3.close()
        finally:
            try:
                store.close()
            except Exception:
                pass
