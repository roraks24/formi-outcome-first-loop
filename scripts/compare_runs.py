import sqlite3

for db in ['runs/mine01/controller.db', 'runs/crash_demo_3pt/controller.db']:
    conn = sqlite3.connect(db)
    print("=" * 60)
    print(db)
    print("=" * 60)
    for row in conn.execute('SELECT proposal_id, workflow_id, applied_version, created_minute, applied_minute, diff_json FROM proposals WHERE status = "APPLIED" ORDER BY applied_minute'):
        print(f"  {row[0]} | wid={row[1]} v{row[2]} | created={row[3]} applied={row[4]} | diff={row[5]}")
