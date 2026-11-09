import sqlite3

conn = sqlite3.connect("runs/mine03/controller.db")
cur = conn.cursor()
cur.execute("""
    SELECT experiment_id, workflow_id, dim, control_arm, arms_json, 
           status, created_minute, applied_minute, concluded_minute
    FROM experiments 
    WHERE status = 'WINNER'
""")
winners = cur.fetchall()
print(f"Total WINNERs: {len(winners)}")
for w in winners:
    exp_id, wid, dim, ctrl, arms, st, c_min, a_min, end_min = w
    print(f"- {exp_id}: workflow={wid}, dim={dim}, ctrl={ctrl}, arms={arms}, concluded_minute={end_min}")
    cur.execute("""
        SELECT arm, n_control, k_control, n_treatment, k_treatment, p_value_primary, decision
        FROM analyses
        WHERE experiment_id = ?
        ORDER BY sim_minute DESC LIMIT 1
    """, (exp_id,))
    an = cur.fetchone()
    if an:
        arm, nc, kc, nt, kt, p, dec = an
        ctrl_rate = kc / nc if nc else 0
        treat_rate = kt / nt if nt else 0
        print(f"  Analysis: arm={arm}, nc={nc} ({ctrl_rate:.4f}), nt={nt} ({treat_rate:.4f}), p={p:.6f}, decision={dec}")
    
    cur.execute("SELECT proposal_id, status, applied_version, diff_json, rationale FROM proposals WHERE workflow_id = ? AND created_minute >= ?", (wid, end_min - 30))
    props = cur.fetchall()
    print(f"  Proposals following conclusion ({len(props)}):")
    for pr in props:
        print(f"    proposal_id={pr[0]}, status={pr[1]}, applied_ver={pr[2]}, diff={pr[3]}")
    print()
conn.close()
