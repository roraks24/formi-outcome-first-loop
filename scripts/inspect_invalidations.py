import sqlite3
import json

for db_path in ['runs/mine01/controller.db', 'runs/mine03/controller.db']:
    print(f"=== {db_path} ===")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT experiment_id, workflow_id, dim, status, created_minute, concluded_minute FROM experiments WHERE status = 'INVALIDATED';")
    rows = cur.fetchall()
    print(f"INVALIDATED experiments count: {len(rows)}")
    for r in rows:
        exp_id, wid, dim, status, c_min, end_min = r
        print(f"\nExperiment: {exp_id} on {wid} ({dim}) created={c_min} concluded={end_min}")
        cur.execute("SELECT decision_id, sim_minute, decision_type, outcome, numbers_json FROM decisions WHERE inputs_ref_json LIKE ? OR outcome LIKE ?;", (f"%{exp_id}%", f"%{exp_id}%"))
        decs = cur.fetchall()
        for d in decs:
            print(f"  Decision: id={d[0]} min={d[1]} type={d[2]} outcome={d[3]} numbers={d[4]}")
        cur.execute("SELECT version, actor, applied_at_minute FROM config_snapshots WHERE workflow_id = ? ORDER BY applied_at_minute;", (wid,))
        snaps = cur.fetchall()
        print("  Config snapshots for workflow:")
        for s in snaps:
            print(f"    v{s[0]} by {s[1]} at min={s[2]}")
