with open('controller/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = """                            p_kpi = w_spec.get("objective", {}).get("primary", "conclusion_rate")
                            g_kpi = w_spec.get("objective", {}).get("guardrail", "pickup_rate")"""

replacement = """                            p_kpi = dry_run.get("primary_kpi") or (w_spec.get("objective", {}).get("primary") if isinstance(w_spec.get("objective", {}).get("primary"), str) else "conclusion_rate")
                            g_kpi = dry_run.get("guardrail_kpi") or ("pickup_rate" if p_kpi == "engagement_rate" else "engagement_rate")"""

if target in content:
    content = content.replace(target, replacement)
    with open('controller/main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS")
else:
    print("Target not found")
