"""Unit tests for crash-recovery reconciliation in Controller.recover()."""
import json
import sqlite3
import pytest
from unittest.mock import MagicMock
from controller.store import Store
from controller.main import Controller


def test_crash_recovery_pending_intents(tmp_path):
    db_path = tmp_path / "recovery_test.db"
    store = Store(str(db_path))

    # Pre-populate an intent that was written ahead before a simulated crash
    key = "prop_crash_123"
    store.record_intent(
        intent_id=f"int_{key}",
        idempotency_key=key,
        action="request_approval",
        workflow_id="ks_cancel",
        payload={"proposal_id": key, "workflow_id": "ks_cancel", "base_version": 1},
        sim_ts="2027-01-04T11:00:00+05:30",
    )

    # Verify it is in PENDING state
    pending = store.get_pending_intents()
    assert len(pending) == 1
    assert pending[0]["idempotency_key"] == key

    # Setup mock API client to simulate simulator answering after controller restart
    mock_api = MagicMock()
    mock_api.get_state.return_value = {
        "scenario": "test_scenario",
        "sim_minute": 60,
        "ts": "2027-01-04T12:00:00+05:30",
        "finished": False,
        "credits_remaining": 190.0,
    }
    mock_api.get_workflows.return_value = [{"workflow_id": "ks_cancel"}]
    mock_api.get_config.return_value = {
        "workflow_id": "ks_cancel",
        "version": 1,
        "actor": "business_default",
        "config": {"allocations": {"default": {}}},
    }
    # Case A: simulator received the approval before crash
    mock_api.list_approvals.return_value = [
        {"approval_id": "apr_crash_01", "proposal_id": key, "workflow_id": "ks_cancel", "status": "pending"}
    ]
    mock_api.list_chunks.return_value = []

    controller = Controller(sim_url="http://mock-sim", db_path=str(db_path))
    controller.api = mock_api
    controller.ingest.api = mock_api
    controller.store = store

    # Run recover()
    controller.recover()

    # The pending intent must now be marked EXECUTED, not left dangling or re-executed
    pending_after = store.get_pending_intents()
    assert len(pending_after) == 0

    cur = store._conn.cursor()
    cur.execute("SELECT status, response_json FROM intents WHERE idempotency_key = ?", (key,))
    row = cur.fetchone()
    cur.close()
    assert row["status"] == "EXECUTED"
    assert "apr_crash_01" in row["response_json"]

    controller.store.close()


def test_crash_recovery_unreceived_intent(tmp_path):
    db_path = tmp_path / "recovery_test_lost.db"
    store = Store(str(db_path))

    # Pre-populate an intent where the process was killed before HTTP request reached simulator
    key = "prop_lost_456"
    store.record_intent(
        intent_id=f"int_{key}",
        idempotency_key=key,
        action="request_approval",
        workflow_id="ks_cancel",
        payload={"proposal_id": key, "workflow_id": "ks_cancel", "base_version": 1},
        sim_ts="2027-01-04T11:00:00+05:30",
    )

    mock_api = MagicMock()
    mock_api.get_state.return_value = {
        "scenario": "test_scenario",
        "sim_minute": 60,
        "ts": "2027-01-04T12:00:00+05:30",
        "finished": False,
        "credits_remaining": 190.0,
    }
    mock_api.get_workflows.return_value = [{"workflow_id": "ks_cancel"}]
    mock_api.get_config.return_value = {
        "workflow_id": "ks_cancel",
        "version": 1,
        "actor": "business_default",
        "config": {"allocations": {"default": {}}},
    }
    # Approval was never seen by simulator
    mock_api.list_approvals.return_value = []
    mock_api.list_chunks.return_value = []

    controller = Controller(sim_url="http://mock-sim", db_path=str(db_path))
    controller.api = mock_api
    controller.ingest.api = mock_api
    controller.store = store

    controller.recover()

    # Intent must be marked FAILED so controller can re-propose cleanly
    pending_after = store.get_pending_intents()
    assert len(pending_after) == 0

    cur = store._conn.cursor()
    cur.execute("SELECT status FROM intents WHERE idempotency_key = ?", (key,))
    row = cur.fetchone()
    cur.close()
    assert row["status"] == "FAILED"

    controller.store.close()
