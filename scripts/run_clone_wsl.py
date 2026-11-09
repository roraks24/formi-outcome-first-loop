import os
import subprocess
import time

CLONE_DIR = "/home/rorak/test_clone"
os.chdir(CLONE_DIR)

# 1. Baseline
base_dir = os.path.join(CLONE_DIR, "runs/base01")
if not os.path.exists(os.path.join(base_dir, "summary.json")):
    os.makedirs(base_dir, exist_ok=True)
    subprocess.run([".venv/bin/python", "-m", "sim", "baseline", "--scenario", "scenarios/public_01_dev.yaml", "--run-dir", "runs/base01"], check=True)

# 2. Server
mine_dir = os.path.join(CLONE_DIR, "runs/mine01")
os.makedirs(mine_dir, exist_ok=True)
server_proc = subprocess.Popen([".venv/bin/python", "-m", "sim", "serve", "--scenario", "scenarios/public_01_dev.yaml", "--run-dir", "runs/mine01", "--port", "8710", "--force"])

time.sleep(2)

# 3. Controller via run.sh
subprocess.run(["./run.sh", "http://127.0.0.1:8710", "runs/mine01/controller.db"])

try:
    server_proc.wait(timeout=10)
except Exception:
    server_proc.terminate()

# 4. Grade
res = subprocess.run([".venv/bin/python", "-m", "sim", "grade", "--run", "runs/mine01", "--baseline", "runs/base01"], capture_output=True, text=True)
print(res.stdout)
if res.stderr:
    print(res.stderr)
