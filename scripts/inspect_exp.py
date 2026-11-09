import sqlite3

for db_path in ['runs/mine01/controller.db', 'runs/crash_demo_3pt/controller.db']:
    print("=" * 60)
    print(db_path)
    print("=" * 60)
    conn = sqlite3.connect(db_path)
    for row in conn.execute('SELECT experiment_id, workflow_id, dim, status, created_minute, applied_minute, concluded_minute FROM experiments ORDER BY created_minute'):
        print(f"  {row[0]} | wid={row[1]} dim={row[2]} st={row[3]} | cr={row[4]} ap={row[5]} end={row[6]}")
