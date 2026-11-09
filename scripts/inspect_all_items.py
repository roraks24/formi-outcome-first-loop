import sqlite3

def run():
    conn = sqlite3.connect("runs/mine03/controller.db")
    cur = conn.cursor()
    print("=== PUBLIC_03 ANALYSES ===")
    cur.execute("""
        SELECT a.analysis_id, a.experiment_id, e.workflow_id, e.dim, a.arm, 
               a.n_control, a.n_treatment, a.p_value_primary, a.srm_p_value, a.decision, e.status
        FROM analyses a
        JOIN experiments e ON a.experiment_id = e.experiment_id
    """)
    for r in cur.fetchall():
        print(r)

    print("\n=== PUBLIC_03 EXPERIMENTS ===")
    cur.execute("""
        SELECT experiment_id, workflow_id, dim, status, applied_version, created_minute, concluded_minute
        FROM experiments
    """)
    for r in cur.fetchall():
        print(r)

run()
