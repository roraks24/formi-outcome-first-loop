import sqlite3
import json

conn = sqlite3.connect('runs/mine02/controller.db')
cur = conn.cursor()
cur.execute("SELECT * FROM decisions WHERE decision_type = 'SUPERVISOR_EDIT_INVALIDATION';")
rows = cur.fetchall()
print(f"SUPERVISOR_EDIT_INVALIDATION ({len(rows)}):")
for r in rows:
    print(r)

cur.execute("SELECT * FROM decisions WHERE decision_type = 'WINNER_PROMOTED';")
rows = cur.fetchall()
print(f"\nWINNER_PROMOTED ({len(rows)}):")
for r in rows:
    print(r)
