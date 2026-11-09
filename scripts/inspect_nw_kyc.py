import sqlite3
import json

conn = sqlite3.connect('runs/mine02/controller.db')
cur = conn.cursor()

print('=== PROPOSALS FOR nw_kyc ===')
cur.execute('SELECT proposal_id, approval_id, base_version, applied_version, status, change_summary, rationale, created_minute, applied_minute FROM proposals WHERE workflow_id = "nw_kyc";')
for r in cur.fetchall():
    print(r)

print('\n=== EXPERIMENTS FOR nw_kyc ===')
cur.execute('SELECT experiment_id, dim, control_arm, arms_json, status, created_minute, concluded_minute FROM experiments WHERE workflow_id = "nw_kyc";')
for r in cur.fetchall():
    print(r)

print('\n=== DECISIONS FOR nw_kyc ===')
cur.execute('SELECT decision_id, sim_minute, decision_type, outcome, numbers_json FROM decisions WHERE workflow_id = "nw_kyc";')
for r in cur.fetchall():
    print(r)
