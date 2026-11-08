"""Tests for write-ahead intent logging, idempotency keys, and stale base handling."""
import json
import sqlite3
import pytest
from controller.store import Store
from controller.governance import compute_config_diff, total_variation_shift


def test_intent_lifecycle(tmp_path):
    db_path = tmp_path / "test_intents.db"
    store = Store(str(db_path))

    # 1. Record intent
    intent_id = "int_001"
    idempotency_key = "prop_abc_123"
    action = "request_approval"
    payload = {"proposal_id": idempotency_key, "workflow_id": "ks_cancel", "base_version": 1}

    store.record_intent(
        intent_id=intent_id,
        idempotency_key=idempotency_key,
        action=action,
        workflow_id="ks_cancel",
        payload=payload,
        sim_ts="2027-01-04T10:00:00+05:30",
    )

    pending = store.get_pending_intents()
    assert len(pending) == 1
    assert pending[0]["idempotency_key"] == idempotency_key
    assert pending[0]["status"] == "PENDING"

    # 2. Duplicate intent with same idempotency key fails UNIQUE constraint
    with pytest.raises(sqlite3.IntegrityError):
        store.record_intent(
            intent_id="int_002",
            idempotency_key=idempotency_key,  # same key!
            action=action,
            workflow_id="ks_cancel",
            payload=payload,
            sim_ts="2027-01-04T10:00:00+05:30",
        )

    # 3. Mark intent executed
    resp = {"approval_id": "apr_00001", "status": "pending"}
    store.mark_intent_executed(idempotency_key, "2027-01-04T10:01:00+05:30", resp)

    pending_after = store.get_pending_intents()
    assert len(pending_after) == 0

    # Verify executed status in database
    cur = store._conn.cursor()
    cur.execute("SELECT status, response_json FROM intents WHERE idempotency_key = ?", (idempotency_key,))
    row = cur.fetchone()
    cur.close()
    assert row["status"] == "EXECUTED"
    assert json.loads(row["response_json"])["approval_id"] == "apr_00001"

    store.close()


def test_stale_base_diff_detection():
    old_cfg = {
        "prompt_version": "P1",
        "allocations": {
            "default": {
                "voice": {"V1": 1.0},
                "caller_id": {"D1": 0.5, "D2": 0.5},
            }
        },
    }

    # Same config = empty diff (no change)
    same_cfg = {
        "prompt_version": "P1",
        "allocations": {
            "default": {
                "voice": {"V1": 1.0},
                "caller_id": {"D1": 0.5, "D2": 0.5},
            }
        },
    }
    assert compute_config_diff(old_cfg, same_cfg) == {}

    # Modified voice
    new_cfg = {
        "prompt_version": "P1",
        "allocations": {
            "default": {
                "voice": {"V1": 0.6, "V2": 0.4},
                "caller_id": {"D1": 0.5, "D2": 0.5},
            }
        },
    }
    diff = compute_config_diff(old_cfg, new_cfg)
    assert "allocations.default.voice" in diff
    assert diff["allocations.default.voice"]["old"] == {"V1": 1.0}

    # TV distance calculation
    tv = total_variation_shift(old_cfg, new_cfg)
    # 0.5 * (|1.0 - 0.6| + |0.0 - 0.4|) = 0.5 * (0.4 + 0.4) = 0.4
    assert pytest.approx(tv, 1e-4) == 0.4
