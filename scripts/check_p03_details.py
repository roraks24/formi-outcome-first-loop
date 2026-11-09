import sqlite3

def check_p03():
    conn = sqlite3.connect("runs/mine03/controller.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT experiment_id, workflow_id, dim, status, applied_version, created_minute, concluded_minute
        FROM experiments
    """)
    rows = cur.fetchall()
    print("Experiments in mine03:")
    for r in rows:
        print(r)

    print("\nAnalyses in mine03:")
    cur.execute("""
        SELECT a.experiment_id, e.workflow_id, e.dim, a.arm, a.n_control, a.n_treatment, a.p_value_primary, a.srm_p_value, a.decision
        FROM analyses a
        JOIN experiments e ON a.experiment_id = e.experiment_id
    """)
    for r in cur.fetchall():
        print(r)

check_p03()
