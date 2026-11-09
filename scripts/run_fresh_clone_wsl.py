import subprocess

cmd = """
rm -rf /home/rorak/fresh_clone
mkdir -p /home/rorak/fresh_clone
git clone /mnt/e/Coding/diwali\\ day\\ hustle /home/rorak/fresh_clone
cd /home/rorak/fresh_clone
/home/rorak/.local/share/uv/python/cpython-3.11.18-linux-x86_64-gnu/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m sim baseline --scenario scenarios/public_01_dev.yaml --run-dir runs/base01 > /dev/null 2>&1 || true
python -m sim serve --scenario scenarios/public_01_dev.yaml --run-dir runs/fresh_run --port 8715 --force > sim.log 2>&1 &
SIM_PID=$!
sleep 4
./run.sh http://127.0.0.1:8715 runs/fresh_run/controller.db
python -m sim grade --baseline runs/base01 --run runs/fresh_run --scenario scenarios/public_01_dev.yaml
kill -9 $SIM_PID 2>/dev/null || true
"""

res = subprocess.run(["wsl", "bash", "-c", cmd], capture_output=True, text=True)
print("=== STDOUT ===")
print(res.stdout)
print("=== STDERR ===")
print(res.stderr)
