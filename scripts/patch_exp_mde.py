with open("controller/experiments.py", "r", encoding="utf-8") as f:
    content = f.read()

target1 = """    status: str
    created_minute: int
    applied_minute: int | None = None"""
repl1 = """    status: str
    created_minute: int
    mde: float = 0.08
    applied_minute: int | None = None"""

target2 = """                applied_minute=r["applied_minute"] if "applied_minute" in r.keys() else None,
                applied_version=r["applied_version"] if "applied_version" in r.keys() else None,"""
repl2 = """                mde=float(r["mde"]) if "mde" in r.keys() and r["mde"] is not None else 0.08,
                applied_minute=r["applied_minute"] if "applied_minute" in r.keys() else None,
                applied_version=r["applied_version"] if "applied_version" in r.keys() else None,"""

target3 = """                status="PENDING_APPLY",
                created_minute=sim_minute,
                applied_minute=None,"""
repl3 = """                status="PENDING_APPLY",
                created_minute=sim_minute,
                mde=float(mde),
                applied_minute=None,"""

for t, r in [(target1, repl1), (target2, repl2), (target3, repl3)]:
    t_crlf = t.replace("\n", "\r\n")
    r_crlf = r.replace("\n", "\r\n")
    if t_crlf in content:
        content = content.replace(t_crlf, r_crlf)
        print("Replaced CRLF")
    elif t in content:
        content = content.replace(t, r)
        print("Replaced LF")
    else:
        print("NOT FOUND:", t[:30])

with open("controller/experiments.py", "w", encoding="utf-8", newline="") as f:
    f.write(content)
print("Finished patching.")
