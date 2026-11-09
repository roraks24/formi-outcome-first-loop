import sqlite3
import glob

for db_path in sorted(glob.glob('runs/*/controller.db')):
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT decision_id, sim_minute, ts, workflow_id, decision_type, inputs_ref_json, numbers_json, policy_clause, outcome FROM decisions WHERE decision_type LIKE '%HARM%' OR outcome LIKE '%HARM%';")
        rows = cur.fetchall()
        print(f"=== {db_path}: {len(rows)} harm stop decisions ===")
        for r in rows:
            print(f"ID: {r['decision_id']}, min: {r['sim_minute']}, ts: {r['ts']}, wf: {r['workflow_id']}, type: {r['decision_type']}")
            print(f"  inputs: {r['inputs_ref_json']}")
            print(f"  numbers: {r['numbers_json']}")
            print(f"  clause: {r['policy_clause']}")
            print(f"  outcome: {r['outcome']}")
        conn.close()
    except Exception as e:
        print(f"{db_path}: {e}")
