import sqlite3
import json

conn = sqlite3.connect('runs/mine02/controller.db')
cur = conn.cursor()

print("=== EXPERIMENTS FOR nw_kyc ===")
cur.execute("SELECT experiment_id, dim, control_arm, arms_json, status, primary_kpi, guardrail_kpi, required_n_per_arm, created_minute, concluded_minute FROM experiments WHERE workflow_id = 'nw_kyc';")
for r in cur.fetchall():
    print(r)

print("\n=== DECISIONS FOR nw_kyc ===")
cur.execute("SELECT decision_id, sim_minute, decision_type, outcome, numbers_json FROM decisions WHERE workflow_id = 'nw_kyc';")
for r in cur.fetchall():
    print(r)
