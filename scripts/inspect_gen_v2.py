import sqlite3
import json

def run():
    conn = sqlite3.connect("runs/mine_gen_v2/controller.db")
    cur = conn.cursor()
    print("=== PROPOSALS FOR APEX_COLLECTIONS ===")
    cur.execute("""
        SELECT proposal_id, approval_id, workflow_id, base_version, applied_version, 
               status, created_minute, decided_minute, applied_minute, diff_json, rejection_reasons_json
        FROM proposals
        WHERE workflow_id = 'apex_collections'
        ORDER BY created_minute ASC
    """)
    for r in cur.fetchall():
        print(f"ID={r[0]} apr_id={r[1]} base_v={r[3]} applied_v={r[4]} status={r[5]} created={r[6]} decided={r[7]} applied={r[8]}")
        print(f"   diff={r[9]}")
        print(f"   rejections={r[10]}")

    print("\n=== FLAGGED DID SHARE BY DAY (apex_collections in gen_v2) ===")
    cur.execute("""
        SELECT sim_day, 
               sum(CASE WHEN caller_id = 'DID_92' THEN dials ELSE 0 END) as flagged_dials,
               sum(dials) as total_dials
        FROM events_agg
        WHERE workflow_id = 'apex_collections'
        GROUP BY sim_day
        ORDER BY sim_day
    """)
    for r in cur.fetchall():
        day, flag, tot = r
        pct = (flag / tot * 100) if tot else 0
        print(f"Day {day}: flagged={flag}, total={tot}, share={pct:.2f}%")

    print("\n=== PROPOSALS FOR KS_CANCEL (public_02) ===")
    conn2 = sqlite3.connect("runs/mine02/controller.db")
    cur2 = conn2.cursor()
    cur2.execute("""
        SELECT proposal_id, approval_id, workflow_id, base_version, applied_version, 
               status, created_minute, decided_minute, applied_minute, diff_json, rejection_reasons_json
        FROM proposals
        WHERE workflow_id = 'ks_cancel'
        ORDER BY created_minute ASC
    """)
    for r in cur2.fetchall():
        print(f"ID={r[0]} apr_id={r[1]} base_v={r[3]} applied_v={r[4]} status={r[5]} created={r[6]} decided={r[7]} applied={r[8]}")
        print(f"   diff={r[9]}")
        print(f"   rejections={r[10]}")

    print("\n=== FLAGGED DID SHARE BY DAY (ks_cancel in public_02) ===")
    cur2.execute("""
        SELECT sim_day, 
               sum(CASE WHEN caller_id = 'DID_4' THEN dials ELSE 0 END) as flagged_dials,
               sum(dials) as total_dials
        FROM events_agg
        WHERE workflow_id = 'ks_cancel'
        GROUP BY sim_day
        ORDER BY sim_day
    """)
    for r in cur2.fetchall():
        day, flag, tot = r
        pct = (flag / tot * 100) if tot else 0
        print(f"Day {day}: flagged={flag}, total={tot}, share={pct:.2f}%")

run()
