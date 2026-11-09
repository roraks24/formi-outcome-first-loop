import sqlite3
import json

conn = sqlite3.connect('runs/mine02/controller.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Check early harm stop proposals
cur.execute("SELECT * FROM proposals WHERE change_summary LIKE '%Early harm%' LIMIT 1;")
row = cur.fetchone()
if row:
    print("=== WORKED EXAMPLE 1: EARLY HARM STOP ===")
    for k in row.keys():
        print(f"  {k}: {row[k]}")

# Check caller ID rotation proposals
cur.execute("SELECT * FROM proposals WHERE change_summary LIKE '%Rotate%' LIMIT 1;")
row2 = cur.fetchone()
if row2:
    print("\n=== WORKED EXAMPLE 2: CALLER-ID ROTATION ===")
    for k in row2.keys():
        print(f"  {k}: {row2[k]}")

# Check hallucination proposals
cur.execute("SELECT * FROM proposals WHERE change_summary LIKE '%Remediate%' LIMIT 1;")
row3 = cur.fetchone()
if row3:
    print("\n=== WORKED EXAMPLE 3: PROMPT REMEDIATION ===")
    for k in row3.keys():
        print(f"  {k}: {row3[k]}")

conn.close()
