import sqlite3
import json

conn = sqlite3.connect('runs/mine02/controller.db')
cur = conn.cursor()

cur.execute("SELECT welcome_message, SUM(dials), SUM(connected), SUM(engaged) FROM events_agg WHERE workflow_id = 'nw_kyc' AND (sim_day * 24 + sim_hour) >= 113 GROUP BY welcome_message;")
print("Observed from 6810//60:")
for r in cur.fetchall():
    print(r)

cur.execute("SELECT * FROM experiments WHERE experiment_id = 'exp_welcome_message_nw_kyc_v2_501961a593f310d3';")
print("\nExperiment row:")
row = cur.fetchone()
print(row)
