import sqlite3
import glob

dbs = [
    ("public_01", "runs/mine01/controller.db"),
    ("public_02", "runs/mine02/controller.db"),
    ("public_03", "runs/mine03/controller.db"),
    ("generalization_v2", "runs/mine_gen_v2/controller.db"),
]

for label, db_path in dbs:
    print(f"==================================================")
    print(f"DATABASE: {label} ({db_path})")
    print(f"==================================================")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Check experiments status counts
        cur.execute("SELECT status, count(*) as cnt FROM experiments GROUP BY status;")
        exp_counts = {r["status"]: r["cnt"] for r in cur.fetchall()}
        print(f"Experiment Status Counts:")
        for status, cnt in exp_counts.items():
            print(f"  {status}: {cnt}")
        if not exp_counts:
            print("  (no experiments found)")

        # Check analyses outcomes
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='experiment_analyses';")
        if cur.fetchone():
            cur.execute("SELECT srm_passed, guardrail_passed, is_significant, winner_arm, count(*) as cnt FROM experiment_analyses GROUP BY srm_passed, guardrail_passed, is_significant, winner_arm;")
            analyses = cur.fetchall()
            print(f"Analyses Summaries: {len(analyses)} groups")
            for a in analyses:
                print(f"  srm={a['srm_passed']}, guardrail={a['guardrail_passed']}, sig={a['is_significant']}, winner={a['winner_arm']} -> {a['cnt']}")

        # Check decisions types
        cur.execute("SELECT decision_type, count(*) as cnt FROM decisions GROUP BY decision_type;")
        dec_counts = {r["decision_type"]: r["cnt"] for r in cur.fetchall()}
        print(f"Decision Type Counts:")
        for dt, cnt in dec_counts.items():
            print(f"  {dt}: {cnt}")

        # Check promotions
        cur.execute("SELECT decision_id, sim_minute, ts, workflow_id, outcome, inputs_ref_json FROM decisions WHERE decision_type IN ('PROMOTION_PROPOSAL', 'PROMOTION_APPLIED') OR outcome LIKE '%promot%';")
        promos = cur.fetchall()
        print(f"Promotions Count: {len(promos)}")
        for p in promos:
            print(f"  Promo: id={p['decision_id']}, wf={p['workflow_id']}, outcome={p['outcome']}")

        # Check decay detection / rollback
        cur.execute("SELECT decision_id, sim_minute, ts, workflow_id, outcome, inputs_ref_json FROM decisions WHERE decision_type LIKE '%DECAY%' OR outcome LIKE '%decay%';")
        decays = cur.fetchall()
        print(f"Decays Count: {len(decays)}")
        for d in decays:
            print(f"  Decay: id={d['decision_id']}, wf={d['workflow_id']}, outcome={d['outcome']}")

        conn.close()
    except Exception as e:
        print(f"ERROR reading {db_path}: {e}")
    print()
