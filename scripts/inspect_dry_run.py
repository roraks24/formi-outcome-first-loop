import sqlite3
import json

conn = sqlite3.connect("runs/mine01/controller.db")
row = conn.execute("SELECT proposal_id, dry_run_json FROM proposals WHERE workflow_id = 'nw_renewal'").fetchone()
print(row[0])
print(row[1])
