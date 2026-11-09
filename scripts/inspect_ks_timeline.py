import sqlite3
import json

conn = sqlite3.connect('runs/mine02/controller.db')
cur = conn.cursor()

print("=== DETECTIONS FOR ks_cancel caller_id ===")
cur.execute("SELECT detection_id, sim_minute, detector_type, target_value, baseline_val, current_val, p_value, ts FROM detections WHERE workflow_id = 'ks_cancel' AND target_dim = 'caller_id';")
for r in cur.fetchall():
    print(r)

print("\n=== PROPOSALS FOR ks_cancel caller_id ===")
cur.execute("SELECT proposal_id, approval_id, base_version, applied_version, status, change_summary, created_minute, applied_minute FROM proposals WHERE workflow_id = 'ks_cancel' AND change_summary LIKE '%caller%';")
for r in cur.fetchall():
    print(r)

print("\n=== DECISIONS FOR ks_cancel caller_id ===")
cur.execute("SELECT decision_id, sim_minute, ts, decision_type, outcome, numbers_json FROM decisions WHERE workflow_id = 'ks_cancel' AND (decision_type LIKE '%SUBMIT%' OR decision_type LIKE '%APPLY%');")
for r in cur.fetchall():
    print(r)
