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
    assert tv == 0.4


def test_deterministic_idempotency_keys():
    from controller.governance import make_deterministic_key

    wid = "ks_cancel"
    change_class = "caller_id"
    cfg1 = {"allocations": {"default": {"caller_id": {"D1": 0.5, "D2": 0.5}}}}
    # Same config with different key insertion order
    cfg1_permuted = {"allocations": {"default": {"caller_id": {"D2": 0.5, "D1": 0.5}}}}
    base_ver = 2

    key1 = make_deterministic_key(wid, change_class, cfg1, base_ver)
    key1_perm = make_deterministic_key(wid, change_class, cfg1_permuted, base_ver)

    # 1. Exact identity and permutation invariance
    assert key1 == key1_perm
    assert key1.startswith("caller_id_ks_cancel_v2_")

    # 2. STALE base version bump produces distinct key for legitimate re-proposal
    key_v3 = make_deterministic_key(wid, change_class, cfg1, base_version=3)
    assert key_v3 != key1
    assert key_v3.startswith("caller_id_ks_cancel_v3_")

    # 3. Rollback target produces distinct key
    cfg_rollback = {"allocations": {"default": {"caller_id": {"D1": 1.0}}}}
    key_rollback = make_deterministic_key(wid, change_class, cfg_rollback, base_ver)
    assert key_rollback != key1

