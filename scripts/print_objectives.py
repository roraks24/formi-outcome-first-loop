import yaml
import json

scenarios = [
    "scenarios/public_01_dev.yaml",
    "scenarios/public_02_incidents.yaml",
    "scenarios/public_03_load.yaml",
    "scenarios/test_generalization_v2.yaml"
]

for s in scenarios:
    with open(s, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    print(f"=== {data['name']} ({s}) ===")
    for t in data.get("tenants", []):
        for w in t.get("workflows", []):
            wid = w["id"]
            obj = w.get("objective", {})
            pri = obj.get("primary")
            grd = obj.get("guardrail")
            print(f"  workflow: {wid}")
            print(f"    primary:   {pri}")
            print(f"    guardrail: {json.dumps(grd)}")
    print()
