import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
import json
from controller.store import Store
from controller.audit import explain_causal_chain

conn = sqlite3.connect('runs/mine02/controller.db')
cur = conn.cursor()

print("Available decision types in runs/mine02/controller.db:")
cur.execute("SELECT decision_type, COUNT(*) FROM decisions GROUP BY decision_type;")
for r in cur.fetchall():
    print(r)

print("\nSample decisions:")
cur.execute("SELECT decision_id, sim_minute, workflow_id, decision_type, outcome FROM decisions LIMIT 10;")
for r in cur.fetchall():
    print(r)
