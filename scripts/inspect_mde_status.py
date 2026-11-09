import sqlite3
from pathlib import Path

dbs = [
    ("public_01", "runs/mine01/controller.db"),
    ("public_02", "runs/mine02/controller.db"),
    ("public_03", "runs/mine03/controller.db"),
    ("gen_v2", "runs/mine_gen_v2/controller.db"),
]

for name, db_path in dbs:
    if not Path(db_path).exists():
        print(f"=== {name}: DB not found ===")
        continue
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    print(f"==================================================")
    print(f"DB: {name} ({db_path})")
    print(f"==================================================")
    
    print("\n--- experiments.status counts ---")
    cur.execute("SELECT status, count(*) FROM experiments GROUP BY status")
    for r in cur.fetchall():
        print(f"  {r[0]}: {r[1]}")
        
    print("\n--- analyses.decision counts ---")
    cur.execute("SELECT decision, count(*) FROM analyses GROUP BY decision")
    for r in cur.fetchall():
        print(f"  {r[0]}: {r[1]}")

    print("\n--- Concluded Experiments & Dynamic MDE ---")
    cur.execute("""
        SELECT e.experiment_id, e.workflow_id, e.dim, e.mde, e.required_n_per_arm, e.status, 
               GROUP_CONCAT(DISTINCT a.decision) as analysis_decisions
        FROM experiments e
        LEFT JOIN analyses a ON a.experiment_id = e.experiment_id
        WHERE e.status IN ('NO_EFFECT', 'INCONCLUSIVE', 'WINNER', 'HALTED', 'INVALIDATED', 'TIMED_OUT')
        GROUP BY e.experiment_id
        ORDER BY e.workflow_id, e.dim
    """)
    rows = cur.fetchall()
    for r in rows:
        exp_id, wid, dim, mde, req_n, status, dec = r
        print(f"  {exp_id}: wid={wid} dim={dim} mde={mde:.4f} req_n={req_n} status={status} decision={dec}")

    print()
