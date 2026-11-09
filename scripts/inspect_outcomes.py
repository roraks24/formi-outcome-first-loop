import sqlite3

for name, db in [
    ('public_01', 'runs/mine01/controller.db'),
    ('public_02', 'runs/mine02/controller.db'),
    ('public_03', 'runs/mine03/controller.db'),
    ('gen_v2', 'runs/mine_gen_v2/controller.db'),
]:
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT status, count(*) FROM experiments GROUP BY status")
    outcomes = dict(cur.fetchall())
    cur.execute("SELECT count(*) FROM proposals WHERE status = 'APPLIED'")
    applied = cur.fetchone()[0]
    print(f"=== {name} ===")
    print("Outcomes:", outcomes)
    print("Applied count:", applied)
    conn.close()
